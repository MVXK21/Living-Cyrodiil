"""Speech to text while the key is held (step 4b-3), with a fake engine."""

from __future__ import annotations

import asyncio
import threading

import numpy as np

from living_cyrodiil import live_stt, talk as talk_mod
from living_cyrodiil.live_stt import RATE, LiveTranscriber, last_speech_end
from living_cyrodiil.stt import Transcript
from living_cyrodiil.talk import TalkSession


def _speech(seconds: float, level: float = 0.2) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def _quiet(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), np.float32)


class FakeEngine:
    """Says how much audio it was given, so tests can tell which call won."""

    def __init__(self, delay: float = 0.0):
        self.calls: list[int] = []
        self.delay = delay

    def transcribe(self, audio):
        self.calls.append(audio.size)
        if self.delay:
            threading.Event().wait(self.delay)
        return Transcript(f"{audio.size / RATE:.2f}s of words", 0.5, audio.size / RATE, "fake")


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


async def _settle():
    for _ in range(30):
        await asyncio.sleep(0.01)


def test_last_speech_end_finds_the_last_loud_frame():
    audio = np.concatenate([_quiet(0.5), _speech(1.0), _quiet(0.6)])
    assert abs(last_speech_end(audio) / RATE - 1.5) < 0.03
    assert last_speech_end(_quiet(1.0)) == 0
    # A quiet last word still counts as speech.
    tail = np.concatenate([_speech(1.0, 0.3), _quiet(0.3), _speech(0.3, 0.03), _quiet(0.4)])
    assert abs(last_speech_end(tail) / RATE - 1.6) < 0.03


def test_pause_before_release_reuses_the_background_text():
    async def scenario():
        eng = FakeEngine()
        live = LiveTranscriber(eng.transcribe)
        said = np.concatenate([_quiet(0.3), _speech(2.0)])
        live.feed(said)                                  # still talking: nothing yet
        assert eng.calls == []
        paused = np.concatenate([said, _quiet(0.3)])
        live.feed(paused)
        await _settle()
        assert len(eng.calls) == 1
        final = np.concatenate([paused, _quiet(0.4)])    # let go a little later, nothing more said
        transcript, how = await live.finish(final)
        assert len(eng.calls) == 1                       # not transcribed again
        assert transcript.text == f"{paused.size / RATE:.2f}s of words"
        assert transcript.seconds < 0.05 and "ready at release" in how

    _run(scenario())


def test_speech_after_the_pause_transcribes_everything():
    async def scenario():
        eng = FakeEngine()
        live = LiveTranscriber(eng.transcribe)
        first = np.concatenate([_speech(1.0), _quiet(0.3)])
        live.feed(first)
        await _settle()
        final = np.concatenate([first, _speech(1.0), _quiet(0.1)])
        transcript, how = await live.finish(final)
        assert eng.calls == [first.size, final.size]
        assert transcript.text == f"{final.size / RATE:.2f}s of words"
        assert "speech after the last pause" in how

    _run(scenario())


def test_release_without_a_pause_transcribes_at_release():
    async def scenario():
        eng = FakeEngine()
        live = LiveTranscriber(eng.transcribe)
        final = np.concatenate([_speech(1.5), _quiet(0.1)])
        live.feed(final)
        transcript, how = await live.finish(final)
        assert eng.calls == [final.size] and "no pause before release" in how

    _run(scenario())


def test_release_while_the_last_pause_is_still_being_transcribed_waits_for_it():
    async def scenario():
        eng = FakeEngine(delay=0.3)
        live = LiveTranscriber(eng.transcribe)
        audio = np.concatenate([_speech(1.0), _quiet(0.3)])
        live.feed(audio)
        await asyncio.sleep(0.05)                        # released before it's done
        transcript, how = await live.finish(np.concatenate([audio, _quiet(0.05)]))
        assert len(eng.calls) == 1 and "after release" in how
        assert 0.1 < transcript.seconds < 0.5

    _run(scenario())


class SnapshotRecorder:
    """A fake mic whose audio grows over time: speech, then a pause."""

    def __init__(self):
        self.audio = np.concatenate([_speech(1.0), _quiet(0.5)])
        self.started_at = 0.0
        self.loop_time = None

    def start(self):
        import time
        self.started_at = time.perf_counter()

    def _so_far(self):
        import time
        n = int((time.perf_counter() - self.started_at) * 4 * RATE)     # 4x speed
        return self.audio[: min(n, self.audio.size)]

    def snapshot(self):
        return self._so_far().copy()

    def stop(self):
        return self.audio.copy()


def test_talk_session_uses_the_text_made_while_held(monkeypatch):
    monkeypatch.setattr(talk_mod, "MIN_HOLD_SECONDS", 0.0)
    monkeypatch.setattr(talk_mod, "POLL_SECONDS", 0.01)

    async def scenario():
        rec, eng = SnapshotRecorder(), FakeEngine()

        async def notify(text):
            return True

        s = TalkSession(lambda: rec, lambda: eng, notify)
        await asyncio.sleep(0.05)                        # engine loaded
        got = []

        async def handler(sheet, transcript, timing):
            got.append((transcript.text, timing.stt))

        s.on_transcript = handler
        await s.talk_start({"npc": {"ref_id": "1", "name": "Witseidutsei", "race": "Argonian", "sex": "female"}})
        await asyncio.sleep(0.6)                         # the whole fake clip plays, pause included
        await s.talk_end({})
        await _settle()
        assert len(got) == 1
        assert len(eng.calls) == 1                       # only the background one
        assert got[0][1] < 0.05                          # nothing left to do at release

    _run(scenario())
