"""Push-to-talk in game (Phase 2 step 5).

The plugin sends talk_start (with the NPC's character sheet) when the talk
key goes down, and talk_end or talk_cancel when it comes up. This module
records the microphone in between, transcribes it, and hands the text on.

Typed input (Phase 5): talk_text carries what the player typed; it's handed
on the same way, with no microphone or speech-to-text. With speaking off
([speech] enabled = false) neither is loaded at all.

In step 5 the text is just shown back to the player ("You said: ...").
Step 6 replaces `on_transcript` with "ask the NPC and show their reply".

Blocking work (opening the mic, Whisper) runs in worker threads so the
connection keeps reading messages meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

import numpy as np

from .character import CharacterSheet
from .live_stt import POLL_SECONDS, LiveTranscriber
from .stt import Transcript

log = logging.getLogger(__name__)

MIN_HOLD_SECONDS = 0.3     # shorter key presses are taps, not speech


class RecorderLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> np.ndarray: ...


class SpeechToTextLike(Protocol):
    def transcribe(self, audio: np.ndarray) -> Transcript: ...


@dataclass
class TurnTiming:
    held: float = 0.0
    stt: float = 0.0


TranscriptHandler = Callable[[CharacterSheet, Transcript, TurnTiming], Awaitable[None]]
Notify = Callable[[str], Awaitable[bool]]


class TalkSession:
    def __init__(self, recorder_factory: Callable[[], RecorderLike],
                 stt_loader: Callable[[], SpeechToTextLike], notify: Notify, live: bool = True,
                 speaking: bool = True):
        self._recorder_factory = recorder_factory
        self._recorder: RecorderLike | None = None
        self._notify = notify
        self._sheet: CharacterSheet | None = None
        self._recording = False
        self._started_at = 0.0
        self._start_task: asyncio.Task | None = None
        # Transcribing at pauses while the key is held (live_stt.py).
        self._live_enabled = live
        self._live: LiveTranscriber | None = None
        self._poll_task: asyncio.Task | None = None
        self.speaking = speaking
        self._told_speaking_off = False
        # Load Whisper now, in the background, so the first turn isn't slow.
        self._stt_task = asyncio.create_task(asyncio.to_thread(stt_loader)) if speaking else None
        self.on_transcript: TranscriptHandler = self._show_transcript
        # Called when the player lets go of the key after speaking (not on a
        # tap), e.g. to warm the LLM connection during speech-to-text.
        self.on_release: Callable[[], None] | None = None

    # -- messages from the game ---------------------------------------------

    async def talk_text(self, msg: dict) -> None:
        """A typed line (Phase 5): answered like a spoken one."""
        npc = msg.get("npc")
        text = " ".join(str(msg.get("text", "")).split())
        if not isinstance(npc, dict) or not text:
            log.warning("talk_text without an npc sheet or text: %r", msg)
            return
        sheet = CharacterSheet.from_game(npc)
        print(f"\n  Typed to {sheet.summary()}")
        log.info("Typed to %r: %r", sheet, text)
        transcript = Transcript(text, 0.0, 0.0, "typed")
        await self.on_transcript(sheet, transcript, TurnTiming())

    async def talk_start(self, msg: dict) -> None:
        if not self.speaking:
            if not self._told_speaking_off:
                self._told_speaking_off = True
                await self._notify("Living Cyrodiil: speaking is off in the companion's settings. "
                                   "Press the type key to type instead.")
            return
        npc = msg.get("npc")
        if not isinstance(npc, dict):
            log.warning("talk_start without an npc sheet: %r", msg)
            return
        if self._recording:
            log.info("talk_start while already recording; restarting")
            await self._stop_recording()
        self._sheet = CharacterSheet.from_game(npc)
        print(f"\n  Talking to {self._sheet.summary()}")
        log.info("Talk start: %r", self._sheet)
        self._recording = True
        self._started_at = time.perf_counter()
        self._start_task = asyncio.create_task(self._open_mic())
        self._live = None
        if self._live_enabled and self._stt_task is not None and self._stt_task.done() and not self._stt_task.exception():
            stt = self._stt_task.result()
            self._live = LiveTranscriber(stt.transcribe)
            self._poll_task = asyncio.create_task(self._poll(self._live))

    async def talk_end(self, msg: dict) -> None:
        if not self._recording:
            return
        held = time.perf_counter() - self._started_at
        live, self._live = self._live, None
        audio = await self._stop_recording()
        sheet = self._sheet
        if sheet is None or audio is None:
            return
        if held < MIN_HOLD_SECONDS:
            log.info("Key tap (%.2fs), not speech; nothing to transcribe", held)
            print("  (key tapped: hold it while you speak)")
            if live:
                live.cancel()
            return
        if self.on_release:
            try:
                self.on_release()
            except Exception:
                log.exception("on_release failed")
        # Transcribe in the background so the connection keeps reading.
        asyncio.create_task(self._transcribe(sheet, audio, held, live))

    async def talk_cancel(self, msg: dict) -> None:
        if not self._recording:
            return
        if self._live:
            self._live.cancel()
            self._live = None
        await self._stop_recording()
        reason = msg.get("reason", "")
        log.info("Talk cancelled (%s); recording discarded", reason)
        print(f"  (cancelled{': ' + reason if reason else ''})")

    # -- internals -----------------------------------------------------------

    async def _open_mic(self) -> None:
        try:
            if self._recorder is None:
                self._recorder = await asyncio.to_thread(self._recorder_factory)
            await asyncio.to_thread(self._recorder.start)
        except Exception as e:
            log.exception("Couldn't open the microphone")
            self._recording = False
            await self._notify(f"Living Cyrodiil: couldn't open the microphone ({e}).")

    async def _poll(self, live: LiveTranscriber) -> None:
        """While the key is down: hand the audio so far to the live transcriber."""
        try:
            if self._start_task:
                await asyncio.shield(self._start_task)          # the mic must be open
            while self._recording and self._live is live:
                snapshot = getattr(self._recorder, "snapshot", None)
                if snapshot is None:
                    return                                       # this recorder can't share audio early
                audio = await asyncio.to_thread(snapshot)
                if self._live is live:
                    live.feed(audio)
                await asyncio.sleep(POLL_SECONDS)
        except Exception:
            log.exception("Live speech-to-text stopped")

    async def _stop_recording(self) -> np.ndarray | None:
        self._recording = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        if self._start_task:
            await self._start_task          # the mic must be open before we close it
            self._start_task = None
        if self._recorder is None:
            return None
        try:
            return await asyncio.to_thread(self._recorder.stop)
        except Exception:
            log.exception("Stopping the microphone failed")
            return None

    async def _transcribe(self, sheet: CharacterSheet, audio: np.ndarray, held: float,
                          live: LiveTranscriber | None = None) -> None:
        try:
            if live is not None:
                transcript, how = await live.finish(audio)
                log.info("Speech-to-text %s; %d background transcription(s) while held", how, live.jobs_started)
            else:
                stt = await self._stt_task
                transcript = await asyncio.to_thread(stt.transcribe, audio)
        except Exception as e:
            log.exception("Speech-to-text failed")
            await self._notify(f"Living Cyrodiil: speech-to-text failed ({e}).")
            return
        timing = TurnTiming(held=held, stt=transcript.seconds)
        log.info("Turn: held %.2fs, speech-to-text %.2fs: %r", held, transcript.seconds, transcript.text)
        if not transcript.text:
            print("  (didn't catch any words)")
            await self._notify("Living Cyrodiil: didn't catch that.")
            return
        await self.on_transcript(sheet, transcript, timing)

    async def _show_transcript(self, sheet: CharacterSheet, transcript: Transcript,
                               timing: TurnTiming) -> None:
        print(f"  You said: {transcript.text}   (speech-to-text {timing.stt:.2f}s)")
        await self._notify(f"You said: {transcript.text}")
