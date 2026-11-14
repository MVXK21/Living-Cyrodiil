"""Spoken replies (Phase 3 step 3): each reply part, said aloud in the NPC's
cloned voice, as an MP3 the game plays from the NPC.

How a voiced reply goes (docs/protocol.md):
1. GameChat sends `reply` with `voiced: true` (only when voices are ready).
2. For each part in order, ReplyVoice generates the speech with Pocket TTS,
   trims the silence at the ends, evens out the loudness, encodes an MP3
   like the game's own (44.1 kHz mono 64 kbps) into the staging folder
   companion\\cache\\audio\\, and sends `reply_audio` (ref_id, part, path,
   seconds). If a part fails, `reply_audio` carries `error` instead and the
   plugin says that part silently.
3. The plugin copies each MP3 over the part's placeholder voice file just
   before the NPC says it.

Everything slow (loading the model, building voices) happens in the
background at start-up; until it's done, replies go out unvoiced.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import zlib
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from . import audio, lip
from .lip_model import LipSync
from .character import CharacterSheet
from .game_files import find_data_folder
from .paths import CACHE_DIR, COMPANION_DIR
from .settings import VoiceSettings
from .voices import CACHE_DIR, REFERENCE_RATE, VoiceBank, VoiceChoice, VoiceLibrary, common_voices

log = logging.getLogger(__name__)

AUDIO_DIR = CACHE_DIR / "audio"
# Loudness speech is evened out to: the median of the game's own generic
# lines (331 lines over 17 voice types of Oblivion.esm, measured Nov 7 after
# trimming silence: median 0.110, middle half 0.090-0.133).
SPEECH_RMS = 0.11
PEAK_LIMIT = 0.95
KEEP_SECONDS = 600         # staged MP3s older than this are deleted
FILLERS = ("Hmm.", "Well...", "Ah...", "Now then...")   # MVXK21, Nov 6: all four, one at random

Send = Callable[[dict], Awaitable[bool]]


def load_model_offline_first(model_class, config: Path):
    """Loads Pocket TTS from the files already on this PC, without asking
    Hugging Face about updates. That check needs the internet and printed a
    harmless "unauthenticated requests" warning on every start (MVXK21, Nov 5).
    Only when the files aren't downloaded yet (a player's first run) does it
    go online to fetch them."""
    from huggingface_hub import constants as hf
    was_offline = hf.HF_HUB_OFFLINE
    hf.HF_HUB_OFFLINE = True
    try:
        model = model_class.load_model(config=str(config))
        log.info("Voice model loaded from the files on this PC")
        return model
    except Exception as e:
        log.info("Voice model not on this PC yet (%s: %s); downloading it", type(e).__name__, str(e)[:200])
    finally:
        hf.HF_HUB_OFFLINE = was_offline
    print("Downloading the voice model (first run only, a few hundred MB)...")
    return model_class.load_model(config=str(config))


def even_out(speech: np.ndarray, sample_rate: int) -> np.ndarray:
    """Trim silence at the ends and set the loudness to match the game's own
    voices, without clipping."""
    speech = audio.trim_silence(speech, sample_rate)
    if speech.size == 0:
        return speech
    rms = float(np.sqrt(np.mean(speech ** 2)))
    if rms > 1e-5:
        speech = speech * (SPEECH_RMS / rms)
    # Speech peaks run about 9 times its loudness, so at this level most
    # replies have a few peaks over the limit: turn down just those.
    return audio.limit_peaks(speech.astype(np.float32), sample_rate, PEAK_LIMIT)


class ReplyVoice:
    def __init__(self, settings: VoiceSettings, data_folder: str = "", audio_dir: Path = AUDIO_DIR):
        self.settings = settings
        self.data_folder = data_folder
        self.audio_dir = audio_dir
        self.library: VoiceLibrary | None = None
        self.bank: VoiceBank | None = None
        self.sample_rate = REFERENCE_RATE
        self._turn = 0
        self._task: asyncio.Task | None = None
        self.model_tag = "default"
        self.lips: LipSync | None = None           # Phase 4: set once the Data folder is known

    # -- start-up -------------------------------------------------------------

    def start(self) -> None:
        """Loads everything in a background thread."""
        self._task = asyncio.create_task(asyncio.to_thread(self._load))

    @property
    def ready(self) -> bool:
        return self.bank is not None and self.library is not None

    def _load(self) -> None:
        t = time.perf_counter()
        try:
            data = find_data_folder(self.data_folder)
            if data is None:
                log.warning("Oblivion's Data folder not found: replies won't be voiced "
                            "(set [game] data_folder in the settings)")
                print("Voices: Oblivion's Data folder not found, so replies won't be voiced.")
                return
            if self.settings.lip_sync == "model":
                self.lips = LipSync(data)
            library = VoiceLibrary(data, REFERENCE_RATE)
            voices = common_voices(library)
            self.library = library
            print("Loading voices (Pocket TTS)...")
            # Harmless on Windows (no symlinks in the model cache); keep the window clean.
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            import torch
            from pocket_tts import TTSModel        # sets torch to 1 thread, its fastest
            config = Path(self.settings.config)
            if not config.is_absolute():
                config = COMPANION_DIR / config
            model = load_model_offline_first(TTSModel, config)
            self.sample_rate = model.sample_rate
            self.model_tag = config.stem
            bank = VoiceBank(model, config.stem)
            bank.prepare(voices)
            self._clean_staging(everything=True)
            self.bank = bank
            log.info("Voices ready: %d voice types, model %s, %d CPU threads, in %.1fs",
                     len(voices), config.name, torch.get_num_threads(), time.perf_counter() - t)
            print(f"Voices ready ({len(voices)} voice types, {time.perf_counter() - t:.0f}s).")
            if self.lips is not None:
                # Loads at once, or (first run) builds while the player plays;
                # mouths follow the loudness until it's done.
                self.lips.load_or_build(print)
        except Exception as e:
            log.exception("Couldn't load voices; replies won't be voiced")
            print(f"Voices failed to load ({e}); replies won't be voiced. See the log.")

    # -- one reply ------------------------------------------------------------

    def choose(self, sheet: CharacterSheet) -> VoiceChoice:
        assert self.library is not None
        return self.library.choose(base_id=sheet.base_id, race=sheet.race, voice_race=sheet.voice_race,
                                   female=sheet.female, name=sheet.name)

    def _make_part(self, choice: VoiceChoice, text: str, path: Path) -> float:
        """Generates one part's MP3 at `path`; returns its length in seconds."""
        assert self.bank is not None
        speech = even_out(self.bank.say(choice, text), self.sample_rate)
        if speech.size == 0:
            raise ValueError("Pocket TTS returned no audio")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio.encode_mp3(speech, self.sample_rate))
        tmp.replace(path)                 # the plugin never sees a half-written file
        seconds = audio.duration(speech, self.sample_rate)
        self._make_lip(path, speech, text)
        return seconds

    # -- lip-sync (Phase 4) ------------------------------------------------------

    def _make_lip(self, mp3: Path, speech: np.ndarray, text: str = "") -> None:
        """Writes the part's .lip next to its MP3 (same name), or removes a
        stale one when lip-sync is off. Never fails the part: no lips is
        better than no voice."""
        path = mp3.with_suffix(".lip")
        mode = self.settings.lip_sync
        if mode == "off":
            path.unlink(missing_ok=True)
            return
        try:
            t = time.perf_counter()
            if mode == "test":
                weights, how = lip.test_pattern(audio.duration(speech, self.sample_rate)), "test pattern"
            else:
                lips = self.lips or LipSync(None)        # no model: the loudness stand-in
                weights = lips.weights(speech, self.sample_rate, seed=zlib.crc32(text.encode()))
                how = "model" if lips.ready else "loudness stand-in"
            log.debug("Lips for %s: %d frames, %s, %.0f ms", mp3.name, len(weights), how,
                      1000 * (time.perf_counter() - t))
        except Exception:
            log.exception("Making lips for %s failed; this part has none", mp3.name)
            path.unlink(missing_ok=True)
            return
        tmp = path.with_suffix(".liptmp")
        tmp.write_bytes(lip.encode(weights))
        tmp.replace(path)

    @staticmethod
    def lip_for(mp3: Path) -> str:
        """The `lip` field of a reply_audio message: the .lip next to `mp3`, or ""."""
        path = mp3.with_suffix(".lip")
        return str(path) if path.is_file() else ""

    # -- filler sounds (step 4, MVXK21, Nov 6) -----------------------------------
    # When the reply is slow, the NPC says a short sound in their own voice
    # so the player hears something within about 3 s. MVXK21: all four
    # wordings, one picked at random each time. Each voice's four are made
    # once and saved, so they play instantly.

    def _filler_dir(self) -> Path:
        # The loudness is in the name, so changing SPEECH_RMS remakes them.
        return CACHE_DIR / "fillers" / f"{self.model_tag}_rms{round(SPEECH_RMS * 1000)}"

    def fillers_for(self, choice: VoiceChoice) -> list[tuple[str, Path, float]]:
        """The filler sounds in this voice: (text, MP3, seconds), made the
        first time and saved."""
        folder = self._filler_dir()
        index = folder / f"{choice.key}.json"
        try:
            saved = json.loads(index.read_text(encoding="utf-8"))
            found = [(e["text"], folder / e["file"], float(e["seconds"])) for e in saved]
            if [t for t, _, _ in found] == list(FILLERS) and all(p.exists() for _, p, _ in found):
                return found
        except (OSError, ValueError, KeyError, TypeError):
            pass
        t = time.perf_counter()
        found = []
        for i, text in enumerate(FILLERS, start=1):
            path = folder / f"{choice.key}_{i}.mp3"
            found.append((text, path, self._make_part(choice, text, path)))
        index.write_text(json.dumps([{"text": t_, "file": p.name, "seconds": round(s, 3)}
                                     for t_, p, s in found]), encoding="utf-8")
        log.info("Filler sounds for %s made in %.1fs: %s", choice.key, time.perf_counter() - t,
                 ", ".join(f"{t_!r} {s:.1f}s" for t_, _, s in found))
        return found

    def warm(self, sheet: CharacterSheet) -> None:
        """While the player is still talking: get this NPC's voice and filler
        sounds ready, so neither costs time once the reply is due."""
        if not self.ready:
            return
        try:
            choice = self.choose(sheet)
            self.bank.state(choice)
            if self.settings.filler_after_seconds > 0:
                self.fillers_for(choice)
        except Exception:
            log.exception("Couldn't get %s's voice ready", sheet.name)

    async def send_filler(self, sheet: CharacterSheet, send: Send) -> bool:
        """Sends one filler sound, picked at random, as a reply of its own
        (marked `filler`, so the plugin plays the real reply straight after)."""
        try:
            choice = await asyncio.to_thread(self.choose, sheet)
            fillers = await asyncio.to_thread(self.fillers_for, choice)
        except Exception:
            log.exception("No filler sound for %s", sheet.name)
            return False
        text, path, seconds = random.choice(fillers)
        if self.settings.lip_sync != "off":
            # Made fresh each time from the saved audio (a few ms), so fillers
            # saved before the lip model was built get its lips too.
            speech = await asyncio.to_thread(audio.decode, path.read_bytes(), self.sample_rate)
            await asyncio.to_thread(self._make_lip, path, speech, text)
        turn = self.new_turn()
        log.info("Reply is late: %s says the filler %r", sheet.name, text)
        print(f"  (reply is slow: {sheet.name} says \"{text}\")")
        await send({"type": "reply", "ref_id": sheet.ref_id, "text": text, "chunks": [text],
                    "listener": "player", "voiced": True, "turn": turn, "filler": True})
        await send({"type": "reply_audio", "ref_id": sheet.ref_id, "turn": turn, "part": 1,
                    "path": str(path), "seconds": round(seconds, 3), "lip": self.lip_for(path)})
        return True

    def new_turn(self) -> int:
        """A number for the next voiced reply, sent in `reply` and each
        `reply_audio`, so the plugin never plays an older reply's audio."""
        self._turn += 1
        return self._turn

    async def speak(self, sheet: CharacterSheet, chunks: list[str], send: Send, turn: int = 0) -> float:
        """Voices every part in order, sending `reply_audio` as each is ready.
        Returns the seconds taken in all."""
        started = time.perf_counter()
        turn = turn or self.new_turn()
        try:
            choice = await asyncio.to_thread(self.choose, sheet)
        except Exception as e:
            log.exception("No voice for %s", sheet.name)
            for part in range(1, len(chunks) + 1):
                await send({"type": "reply_audio", "ref_id": sheet.ref_id, "turn": turn, "part": part,
                            "error": str(e)})
            return 0.0
        print(f"  Voice: {choice.label}")
        log.info("Voicing %d part(s) for %s with %s [%s]", len(chunks), sheet.name, choice.label, choice.key)
        await asyncio.to_thread(self._clean_staging)
        for part, text in enumerate(chunks, start=1):
            await self.speak_part(sheet, choice, turn, part, text, send)
        return time.perf_counter() - started

    async def speak_part(self, sheet: CharacterSheet, choice: VoiceChoice | None, turn: int, part: int,
                         text: str, send: Send) -> bool:
        """Voices one part and sends its `reply_audio` (or an `error`).
        `choice` None: pick the NPC's voice first. Returns True if voiced."""
        try:
            if choice is None:
                choice = await asyncio.to_thread(self.choose, sheet)
                log.info("Voicing %s's reply with %s [%s]", sheet.name, choice.label, choice.key)
                if part == 1:
                    print(f"  Voice: {choice.label}")
                    await asyncio.to_thread(self._clean_staging)
            path = self.audio_dir / f"{sheet.ref_id or 'npc'}_{turn}_{part}.mp3"
            t = time.perf_counter()
            seconds = await asyncio.to_thread(self._make_part, choice, text, path)
        except Exception as e:
            log.exception("Voicing part %d for %s failed", part, sheet.name)
            await send({"type": "reply_audio", "ref_id": sheet.ref_id, "turn": turn, "part": part,
                        "error": str(e)})
            return False
        took = time.perf_counter() - t
        log.info("Part %d voiced: %.1fs of speech in %.2fs (%.1fx real time), %s",
                 part, seconds, took, seconds / took if took else 0, path.name)
        await send({"type": "reply_audio", "ref_id": sheet.ref_id, "turn": turn, "part": part,
                    "path": str(path), "seconds": round(seconds, 3), "lip": self.lip_for(path)})
        return True

    def _clean_staging(self, everything: bool = False) -> None:
        if not self.audio_dir.is_dir():
            return
        cutoff = time.time() - KEEP_SECONDS
        for f in self.audio_dir.iterdir():
            try:
                if f.is_file() and (everything or f.stat().st_mtime < cutoff):
                    f.unlink()
            except OSError:
                pass                      # the game may be reading it; next time
