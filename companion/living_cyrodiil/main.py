"""The companion program players run alongside the game.

Waits for the game, completes the handshake and logs game events. Push-to-talk:
records the mic while the talk key is held, transcribes it, asks the LLM for
the NPC's in-character reply (game_chat.py) and sends it to the game as
subtitles. Without an API key, NPCs repeat the player's words instead.
Run with companion\\companion.ps1. Close the window (or Ctrl+C) to stop.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from . import COPYRIGHT, __version__, books, build_mark, character, places, world_events
from .character import CharacterSheet
from .game_files import find_data_folder
from .game_chat import GameChat, reply_message
from .game_link import GameLink
from .llm import LLMClient
from .memory import Memory
from .logs import setup_logging
from .paths import API_KEY_FILE, DATA_DIR, LOG_DIR, SETTINGS_FILE
from .recorder import Recorder
from .setting_profile import load_profile
from .settings import Settings, SettingsError, load_settings
from .stt import make_engine
from .subtitles import split_into_chunks
from .talk import TalkSession
from .voice_out import ReplyVoice
from .world_knowledge import WorldKnowledge, knowledge_path

log = logging.getLogger("companion")


async def run(settings: Settings) -> None:
    port = settings.game_port
    link = GameLink(port=port)
    profile = load_profile(settings.setting_profile)
    sp = settings.speech
    vocabulary = list(profile.stt_vocabulary)

    def load_stt():
        print(f"Loading speech-to-text ({sp.engine})...")
        engine = make_engine(sp.engine, sp.whisper_model, sp.moonshine_model, vocabulary)
        print("Speech-to-text ready.")
        return engine

    talk = TalkSession(
        recorder_factory=lambda: Recorder(sp.mic_device, keep_open=sp.keep_mic_open),
        stt_loader=load_stt,
        notify=link.notify,
        live=sp.transcribe_while_talking,
        speaking=sp.enabled,
    )
    if not sp.enabled:
        print("Speaking is off ([speech] enabled = false): type to NPCs with the type key (F3).")
    # Voices (Phase 3): Pocket TTS and the NPC voices load in the background;
    # replies are subtitles only until they're ready.
    voice: ReplyVoice | None = None
    if settings.voice.enabled:
        voice = ReplyVoice(settings.voice, settings.game_data_folder)
        voice.start()
    else:
        print("Voices are off ([voice] enabled = false): replies are subtitles only.")
    # Naming places from the game's map markers (lore pass, plugin 0.19.0).
    game_data = find_data_folder(settings.game_data_folder)
    character.place_lookup = places.Places(places.load(game_data)).where
    # The game's own books (step 5c), read from the player's Oblivion.esm.
    books.library = books.BookLibrary(books.load(game_data))

    async def echo_as_npc(sheet, transcript, timing) -> None:
        # No API key: the NPC "says" the player's own words, so everything
        # but the LLM can still be tested.
        text = f"You said: {transcript.text}"
        chunks = split_into_chunks(text)
        print(f"  You said: {transcript.text}   (speech-to-text {timing.stt:.2f}s, {len(chunks)} subtitle(s))")
        voiced = voice is not None and voice.ready
        turn = voice.new_turn() if voiced else 0
        await link.send(reply_message(sheet.ref_id, text, chunks, voiced, turn=turn))
        if voiced:
            await voice.speak(sheet, chunks, link.send, turn)

    world = WorldKnowledge(knowledge_path(settings))
    on = world.active()
    print(f"World knowledge: {world.path.name}, " +
          (f"{len(on)} entr{'y' if len(on) == 1 else 'ies'} on ({', '.join(str(e.number) for e in on)})"
           if on else "all entries off"))
    # Quest-stage awareness (step 3b) and the world knowledge mode (step 3c).
    if settings.world_mode != "manual":
        world_events.tracker = world_events.WorldEvents(*world_events.load(DATA_DIR / "quest_events.toml"))
        log.info("World events: %d loaded", len(world_events.tracker.events))
    if settings.world_mode == "automatic":
        world = None
        print("World knowledge: ignored ([world] mode = \"automatic\"): NPCs follow your quest progress only.")
    elif settings.world_mode == "manual":
        print("Quest progress: ignored ([world] mode = \"manual\"): NPCs follow your world knowledge file only.")

    chat: GameChat | None = None
    if settings.has_api_key:
        llm = LLMClient(settings.llm)
        memory = Memory()
        loop = asyncio.get_running_loop()

        def memory_seq(playthrough: str, seq: int) -> None:
            # Called from worker threads: hand the message to the event loop.
            msg = {"type": "memory_seq", "id": playthrough, "seq": seq}
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(link.send(msg)))

        memory.on_seq = memory_seq
        chat = GameChat(profile, llm, send=link.send, notify=link.notify, world=world, voice=voice, memory=memory)
        link.on("playthrough", chat.set_playthrough)
        link.on("talk_over", chat.talk_over)
        talk.on_release = llm.warm_up

        def show_turn(t) -> None:
            print(f"  You: {t.player}")
            print(f"  {t.npc}: {t.reply}")
            note = f"speech-to-text {t.stt:.1f}s, LLM {t.llm:.1f}s, total {t.total:.1f}s"
            if t.attempts > 1:
                note += ", retried"
            if t.used_fallback:
                note += ", used a fallback line"
            print(f"     ({note})")

        chat.on_turn = show_turn
        talk.on_transcript = chat.answer
        link.on("vanilla_line", chat.vanilla_line)
        print(f"NPCs answer with {settings.llm.model}.")
    else:
        talk.on_transcript = echo_as_npc
        print(f"No API key in {(API_KEY_FILE or SETTINGS_FILE).name}: NPCs will repeat your words instead of answering.")
        log.warning("No API key: echo mode")
    async def talk_start(msg: dict) -> None:
        await talk.talk_start(msg)
        # While the player speaks, get this NPC's voice and filler sounds
        # ready (the first talk with a named NPC builds their voice).
        npc = msg.get("npc")
        if voice is not None and voice.ready and isinstance(npc, dict):
            asyncio.create_task(asyncio.to_thread(voice.warm, CharacterSheet.from_game(npc)))

    async def type_start(msg: dict) -> None:
        # The text box is open: get this NPC's voice and the LLM connection
        # ready while the player types.
        npc = msg.get("npc")
        if not isinstance(npc, dict):
            return
        sheet = CharacterSheet.from_game(npc)
        print(f"\n  Typing to {sheet.summary()}")
        log.info("Type start: %r", sheet)
        if voice is not None and voice.ready:
            asyncio.create_task(asyncio.to_thread(voice.warm, sheet))
        warm_up = getattr(chat.llm, "warm_up", None) if chat is not None else None
        if warm_up:
            warm_up()

    async def talk_text(msg: dict) -> None:
        # Answered in the background, so the connection keeps reading.
        asyncio.create_task(talk.talk_text(msg))

    link.on("talk_start", talk_start)
    link.on("talk_end", talk.talk_end)
    link.on("talk_cancel", talk.talk_cancel)
    link.on("type_start", type_start)
    link.on("talk_text", talk_text)

    def connected(hello: dict) -> None:
        print(f"Game connected (plugin {hello.get('plugin_version', '?')}).")

    def disconnected() -> None:
        print("Game disconnected. Waiting for it to come back...")

    def on_event(msg: dict) -> None:
        detail = msg.get("detail")
        print(f"  game event: {msg.get('name')}" + (f" ({detail})" if detail else ""))
        if chat and msg.get("name") in ("save_loaded", "main_menu"):
            chat.forget_all(str(msg.get("name")))
        # First-run help in game (alpha release, Nov 13): the player may never
        # see the companion's window.
        if msg.get("name") == "save_loaded":
            if chat is None:
                where = (f"open the mod's folder (in MO2: right-click the mod, Open in Explorer) and paste it "
                         f"into {API_KEY_FILE.name}, then restart the game" if API_KEY_FILE
                         else f"paste it into {SETTINGS_FILE}, then restart the companion")
                asyncio.ensure_future(link.notify(
                    f"Living Cyrodiil needs your OpenRouter API key: {where}. Until then NPCs repeat your words."))
            if voice is not None and not voice.ready:
                asyncio.ensure_future(link.notify(
                    "Living Cyrodiil is still getting voices ready (the first start downloads about 1 GB and "
                    "takes a few minutes). Replies are subtitles only until then."))

    link.on_connect = connected
    link.on_disconnect = disconnected
    link.on("event", on_event)

    await link.start()
    print(f"Living Cyrodiil companion {__version__} (build {build_mark.code()}). {COPYRIGHT}")
    log.info("Living Cyrodiil companion %s, build %s", __version__, build_mark.code())
    print(f"Waiting for the game on port {port}. Leave this window open while you play.")
    try:
        await asyncio.Event().wait()     # run until the window is closed
    finally:
        await link.stop()


def main() -> int:
    try:
        settings = load_settings(require_key=False)
    except SettingsError as e:
        print(f"\n{e}\n")
        return 1
    setup_logging(settings.log_level)
    log.info("Companion %s starting", __version__)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"Couldn't listen on port {settings.game_port}: {e}\n"
              "Is another copy of the companion already running?")
        log.error("Listen failed: %s", e)
        return 1
    print(f"Companion stopped. Log: {LOG_DIR / 'companion.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
