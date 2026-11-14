"""In-game conversations (Phase 2 step 6e): the player's words -> the NPC's reply.

TalkSession hands each transcript here. GameChat keeps one Conversation per
NPC (by reference id) for the current play session, so an NPC remembers the
last few exchanges with the player. It refreshes the character sheet every
turn (time of day, disposition, location change), runs the LLM call in a
worker thread so the game connection keeps reading, splits the reply into
subtitle chunks and sends it to the game.

Loading a save forgets every conversation: it's a different moment in time,
maybe a different character. Remembering across visits is Phase 5 memory.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import world_events
from .character import CharacterSheet
from .conversation import MAX_HISTORY, Conversation, LLMError
from .prompt import Turn
from . import recall
from .setting_profile import SettingProfile
from .stt import Transcript
from .subtitles import split_into_chunks
from .talk import TurnTiming

log = logging.getLogger(__name__)

MAX_VANILLA_LINES = 6       # recent vanilla dialogue lines kept per NPC
NEW_MEETING_AFTER_DAYS = 1 / 24     # talking again an in-game hour after parting is a new meeting
NEW_MEETING_AFTER_SECONDS = 300     # the same, when the game time isn't known

Send = Callable[[dict], Awaitable[bool]]
Notify = Callable[[str], Awaitable[bool]]


@dataclass
class TurnReport:
    """What happened in one turn, for the log and the console."""
    npc: str
    player: str
    reply: str
    held: float
    stt: float
    llm: float
    total: float          # from key release to the reply being sent
    attempts: int
    used_fallback: bool
    chunks: int


class GameChat:
    def __init__(self, profile: SettingProfile, llm, send: Send, notify: Notify, world=None, voice=None,
                 memory=None):
        self.profile = profile
        self.llm = llm                  # LLMClient or anything with complete(messages)
        self.world = world              # the player's WorldKnowledge, or None
        self.voice = voice              # ReplyVoice (Phase 3), or None for subtitles only
        self._send = send
        self._notify = notify
        self._conversations: dict[str, Conversation] = {}
        self.memory = memory            # Phase 5: memory.Memory, or None
        self._locks: dict[str, asyncio.Lock] = {}
        # Vanilla dialogue lines (step 8) for NPCs we haven't talked to by voice
        # yet, waiting to start their conversation history: (topic, text).
        self._pending_vanilla: dict[str, deque[tuple[str, str]]] = {}
        self.on_turn: Callable[[TurnReport], None] | None = None

    def _attach_memory(self, convo: Conversation, sheet: CharacterSheet) -> None:
        """Starts the conversation from what this NPC remembers of earlier
        visits in this save, and saves each new exchange (Phase 5)."""
        memory = self.memory
        if memory is None or not memory.active or not sheet.ref_id:
            return
        now = sheet.days_passed
        remembered = memory.recent(sheet.ref_id, MAX_HISTORY)
        convo.history = [Turn(player=r.player, npc=r.npc, vanilla=r.vanilla, earlier=True,
                              when=recall.describe_ago(now, r.day)) for r in remembered]
        summaries = memory.summaries(sheet.ref_id, recall.MAX_SUMMARIES)
        convo.memory_note = recall.memory_note(summaries, now)
        if remembered or summaries:
            log.info("[%s] remembers %d earlier exchange(s) and %d earlier conversation(s) from this save",
                     sheet.name, len(remembered), len(summaries))
        who = {"name": sheet.name, "ref_id": sheet.ref_id, "base_id": sheet.base_id, "race": sheet.race,
               "class": sheet.class_name}
        # The day comes from the conversation's sheet, refreshed each time the player talks.
        convo.on_remember = lambda player, said, vanilla: memory.add(
            sheet.ref_id, who, player, said, vanilla, day=convo.sheet.days_passed)

    async def talk_over(self, msg: dict) -> None:
        """The plugin let the NPC go (Phase 5). What was said since the last
        summary is summarised in the background (one small LLM call), for the
        NPC to recall later. Talking again soon carries on the same
        conversation; later, it's a new meeting (`_new_meeting`)."""
        ref = str(msg.get("ref_id") or "")
        convo = self._conversations.get(ref)
        if convo is None:
            return
        convo.over_day, convo.over_at = convo.sheet.days_passed, time.monotonic()
        log.info("[%s] let go (%s)", convo.sheet.name, msg.get("reason") or "?")
        memory = self.memory
        if memory is None or not memory.active:
            return
        lock = self._locks.setdefault(ref, asyncio.Lock())
        async with lock:
            todo = memory.unsummarised(ref)
            if not any(not e.vanilla for e in todo):
                return                       # nothing said with the LLM this time
            sheet = convo.sheet
            earlier = [s["text"] for s in memory.summaries(ref, recall.MAX_SUMMARIES)]
            try:
                text, facts = await asyncio.to_thread(
                    recall.summarize, self.llm, sheet.name, sheet.race, sheet.class_name, todo, earlier)
            except Exception as e:
                log.warning("[%s] couldn't summarise the conversation (%s); the exchanges are still remembered",
                            sheet.name, e)
                return
            memory.add_summary(ref, todo, text, facts, day=sheet.days_passed)
            if not text:
                log.info("[%s] nothing new to remember from this conversation", sheet.name)
                return
            log.info("[%s] remembers this conversation as: %s%s", sheet.name, text,
                     f" (facts: {'; '.join(facts)})" if facts else "")

    def set_playthrough(self, msg: dict) -> None:
        """The plugin says which playthrough the loaded save is (Phase 5)."""
        if self.memory is None:
            return
        pid = str(msg.get("id") or "")
        if not pid:
            return
        self.forget_all("playthrough")
        seq = msg.get("seq")
        self.memory.set_playthrough(pid, int(seq) if isinstance(seq, (int, float)) else -1, str(msg.get("save") or ""))

    def forget_all(self, why: str = "") -> None:
        if self._conversations:
            log.info("Forgetting %d conversation(s)%s", len(self._conversations), f" ({why})" if why else "")
        self._conversations.clear()
        self._locks.clear()
        self._pending_vanilla.clear()

    def vanilla_line(self, msg: dict) -> None:
        """A line the NPC said in the vanilla dialogue window (plugin message).
        It joins that NPC's conversation history in the order it happened, so
        "tell me more" follows on from it, not from an older voice exchange."""
        ref = str(msg.get("ref_id") or "")
        text = " ".join(str(msg.get("text") or "").split())
        topic = str(msg.get("topic") or "")
        if not ref or not text:
            return
        log.info("Vanilla line from %s (%s)%s: %s", msg.get("name") or "?", ref,
                 f", topic {topic!r}" if topic else "", text)
        convo = self._conversations.get(ref)
        if convo is not None:
            convo.add_vanilla(topic, text)
        else:
            self._pending_vanilla.setdefault(ref, deque(maxlen=MAX_VANILLA_LINES)).append((topic, text))

    def conversation_for(self, sheet: CharacterSheet) -> Conversation:
        # Quest news spreads from the day it happened in this playthrough (step 3b).
        if world_events.tracker is not None:
            world_events.tracker.use_playthrough(self.memory.folder if self.memory else None)
        key = sheet.ref_id or sheet.name
        convo = self._conversations.get(key)
        if convo is None:
            convo = Conversation(sheet, self.profile, self.llm, self.world)
            self._conversations[key] = convo
            log.info("New conversation with %s (%s)", sheet.name, key)
            self._attach_memory(convo, sheet)
            for topic, text in self._pending_vanilla.pop(key, ()):
                convo.add_vanilla(topic, text)
        elif self._new_meeting(convo, sheet):
            # They parted a while ago: start again from memory, as a new meeting.
            log.info("[%s] a new meeting (the last one ended a while ago)", sheet.name)
            del self._conversations[key]
            return self.conversation_for(sheet)
        else:
            convo.sheet = sheet         # fresh from the game: time, disposition, place
            convo.over_day = convo.over_at = None
        return convo

    @staticmethod
    def _new_meeting(convo: Conversation, sheet: CharacterSheet) -> bool:
        """Talking again after the NPC was let go: the same conversation within
        an in-game hour (about 2 real minutes at the usual time scale), or 5
        real minutes when the game time isn't known; a new meeting after."""
        if convo.over_at is None:
            return False
        if convo.over_day is not None and sheet.days_passed is not None:
            return sheet.days_passed - convo.over_day > NEW_MEETING_AFTER_DAYS
        return time.monotonic() - convo.over_at > NEW_MEETING_AFTER_SECONDS

    # -- filler sound when the reply is late (step 4, MVXK21, Nov 6) ------------

    def _start_filler_timer(self, sheet: CharacterSheet, released_at: float) -> asyncio.Task | None:
        """If no reply is ready `filler_after_seconds` after the key came up,
        the NPC says a short filler sound. Returns the timer (or None)."""
        voice = self.voice
        if voice is None or not voice.ready or voice.settings.filler_after_seconds <= 0:
            return None
        delay = released_at + voice.settings.filler_after_seconds - time.perf_counter()
        state = {"sending": False}

        async def later() -> None:
            await asyncio.sleep(max(0.0, delay))
            state["sending"] = True
            await voice.send_filler(sheet, self._send)

        task = asyncio.create_task(later())
        task.state = state              # type: ignore[attr-defined]
        return task

    async def _stop_filler_timer(self, task: asyncio.Task | None) -> None:
        """The reply is ready: cancel the filler if it hasn't started; if it's
        being sent, let it finish so it reaches the game before the reply."""
        if task is None:
            return
        if not task.state["sending"]:   # type: ignore[attr-defined]
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Filler sound failed")

    async def answer(self, sheet: CharacterSheet, transcript: Transcript, timing: TurnTiming) -> None:
        """Called with each transcript; sends the NPC's reply to the game."""
        released_at = time.perf_counter() - timing.stt     # roughly when the key came up
        key = sheet.ref_id or sheet.name
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:                # one turn at a time per NPC
            convo = self.conversation_for(sheet)
            filler = self._start_filler_timer(sheet, released_at)
            if self._can_stream():
                await self._answer_streamed(sheet, convo, transcript, timing, released_at, filler)
                return
            try:
                info = await asyncio.to_thread(convo.reply, transcript.text)
            except LLMError as e:
                log.error("LLM call failed for %s: %s", sheet.name, e)
                await self._stop_filler_timer(filler)
                await self._notify(f"Living Cyrodiil: {sheet.name} couldn't answer ({e}).")
                return
            except Exception as e:      # never let one bad turn stop the companion
                log.exception("Reply failed for %s", sheet.name)
                await self._stop_filler_timer(filler)
                await self._notify(f"Living Cyrodiil: something went wrong getting a reply ({e}).")
                return
            await self._stop_filler_timer(filler)     # a filler already on its way goes first

            chunks = split_into_chunks(info.text)
            voiced = self.voice is not None and self.voice.ready
            turn = self.voice.new_turn() if voiced else 0
            await self._send(reply_message(sheet.ref_id, info.text, chunks, voiced, turn=turn))
            report = TurnReport(
                npc=sheet.name, player=transcript.text, reply=info.text,
                held=timing.held, stt=timing.stt, llm=info.seconds,
                total=time.perf_counter() - released_at,
                attempts=info.attempts, used_fallback=info.used_fallback, chunks=len(chunks),
            )
            log.info("Turn timing for %s: held %.2fs, speech-to-text %.2fs, LLM %.2fs (%d attempt%s%s), "
                     "release to reply %.2fs, %d subtitle(s)",
                     report.npc, report.held, report.stt, report.llm, report.attempts,
                     "" if report.attempts == 1 else "s", ", fallback line" if report.used_fallback else "",
                     report.total, report.chunks)
            if self.on_turn:
                self.on_turn(report)
            if voiced:
                took = await self.voice.speak(sheet, chunks, self._send, turn)
                log.info("Voiced %s's reply in %.2fs", sheet.name, took)


    # -- streamed replies (Phase 3 step 4b) -------------------------------------

    def _can_stream(self) -> bool:
        """Voiced replies stream: part 1 is said while the rest is written.
        Unvoiced ones (voices off or still loading) go out whole, as before."""
        return (self.voice is not None and self.voice.ready
                and callable(getattr(self.llm, "complete_streaming", None)))

    async def _answer_streamed(self, sheet: CharacterSheet, convo: Conversation, transcript: Transcript,
                               timing: TurnTiming, released_at: float, filler: asyncio.Task | None) -> None:
        """Part 1 goes to the game (and is voiced) as soon as it's written;
        then `reply_parts` gives every part, and each later part's audio
        follows (docs/protocol.md)."""
        loop = asyncio.get_running_loop()
        first: asyncio.Future[str] = loop.create_future()

        def on_first_part(text: str) -> None:           # called from the worker thread
            loop.call_soon_threadsafe(lambda: first.done() or first.set_result(text))

        work = asyncio.create_task(asyncio.to_thread(convo.reply_streaming, transcript.text, on_first_part))
        await asyncio.wait({first, work}, return_when=asyncio.FIRST_COMPLETED)
        if not first.done():
            failed = work.exception()
            if failed is None:                          # finished; part 1's callback is still queued
                await first
            else:
                await self._stop_filler_timer(filler)
                if isinstance(failed, LLMError):
                    log.error("LLM call failed for %s: %s", sheet.name, failed)
                    await self._notify(f"Living Cyrodiil: {sheet.name} couldn't answer ({failed}).")
                else:
                    log.error("Reply failed for %s", sheet.name, exc_info=failed)
                    await self._notify(f"Living Cyrodiil: something went wrong getting a reply ({failed}).")
                return
        part1 = first.result()
        await self._stop_filler_timer(filler)           # a filler already on its way goes first

        turn = self.voice.new_turn()
        await self._send(reply_message(sheet.ref_id, part1, [part1], True, turn=turn, streaming=True))
        part1_sent = time.perf_counter() - released_at
        speaking = asyncio.create_task(self.voice.speak_part(sheet, None, turn, 1, part1, self._send))

        try:
            info = await work
            parts = info.parts or [part1]
        except Exception as e:                          # part 1 is out: the reply ends there
            log.error("The rest of %s's reply failed (%s); it ends after part 1", sheet.name, e)
            info, parts = None, [part1]
        await self._send({"type": "reply_parts", "ref_id": sheet.ref_id, "turn": turn, "chunks": parts})
        await speaking
        choice = None
        if len(parts) > 1:
            try:
                choice = await asyncio.to_thread(self.voice.choose, sheet)
            except Exception:
                log.exception("No voice for %s's later parts", sheet.name)
        for part, text in enumerate(parts[1:], start=2):
            await self.voice.speak_part(sheet, choice, turn, part, text, self._send)

        if info is not None:
            report = TurnReport(
                npc=sheet.name, player=transcript.text, reply=info.text,
                held=timing.held, stt=timing.stt, llm=info.first_part_seconds or info.seconds,
                total=part1_sent, attempts=info.attempts, used_fallback=info.used_fallback, chunks=len(parts),
            )
            log.info("Turn timing for %s (streamed): held %.2fs, speech-to-text %.2fs, part 1 written after %.2fs "
                     "(whole reply %.2fs, %d attempt%s%s), release to part 1 sent %.2fs, %d part(s)%s",
                     report.npc, report.held, report.stt, report.llm, info.seconds, report.attempts,
                     "" if report.attempts == 1 else "s", ", fallback line" if report.used_fallback else "",
                     report.total, report.chunks, f", ended early: {info.cut_short}" if info.cut_short else "")
            if self.on_turn:
                self.on_turn(report)


def reply_message(ref_id: str, text: str, chunks: list[str], voiced: bool, listener: str = "player",
                  turn: int = 0, streaming: bool = False) -> dict:
    """The `reply` message (docs/protocol.md). `voiced`: a `reply_audio` per
    part follows, carrying the same `turn`. `streaming`: `chunks` is part 1
    only, and `reply_parts` gives the whole list later. `listener` is who the
    NPC speaks to (Phase 6 adds NPCs)."""
    msg = {"type": "reply", "ref_id": ref_id, "text": text, "chunks": chunks, "listener": listener}
    if voiced:
        msg["voiced"] = True
        msg["turn"] = turn
    if streaming:
        msg["streaming"] = True
    return msg
