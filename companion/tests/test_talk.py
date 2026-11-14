"""Push-to-talk flow with a fake microphone and fake speech-to-text."""

from __future__ import annotations

import asyncio

import numpy as np

from living_cyrodiil import talk as talk_mod
from living_cyrodiil.stt import Transcript
from living_cyrodiil.talk import TalkSession

NPC = {"ref_id": "0001", "name": "Witseidutsei", "race": "Argonian", "sex": "female",
       "class": "Publican", "interior": True, "location": "Three Sisters' Inn", "hour": 20}


class FakeRecorder:
    def __init__(self, fail_start=False):
        self.started = 0
        self.stopped = 0
        self.fail_start = fail_start

    def start(self):
        if self.fail_start:
            raise RuntimeError("no microphone")
        self.started += 1

    def stop(self):
        self.stopped += 1
        return np.full(16_000, 0.3, np.float32)


class FakeSTT:
    def __init__(self, text="Any news from Bravil?"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return Transcript(self.text, 0.05, audio.size / 16_000, "fake")


def _session(recorder=None, stt=None):
    notes: list[str] = []

    async def notify(text):
        notes.append(text)
        return True

    rec = recorder or FakeRecorder()
    engine = stt or FakeSTT()
    session = TalkSession(lambda: rec, lambda: engine, notify)
    return session, rec, engine, notes


async def _settle():
    for _ in range(20):          # let background tasks finish
        await asyncio.sleep(0.01)


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, 10))


def test_hold_speak_release_shows_words(monkeypatch):
    monkeypatch.setattr(talk_mod, "MIN_HOLD_SECONDS", 0.0)

    async def scenario():
        s, rec, stt, notes = _session()
        await s.talk_start({"type": "talk_start", "npc": NPC})
        await asyncio.sleep(0.05)
        await s.talk_end({"type": "talk_end", "ref_id": "0001"})
        await _settle()
        assert rec.started == 1 and rec.stopped == 1 and stt.calls == 1
        assert notes == ["You said: Any news from Bravil?"]

    _run(scenario())


def test_step6_hook_receives_sheet_and_transcript(monkeypatch):
    monkeypatch.setattr(talk_mod, "MIN_HOLD_SECONDS", 0.0)

    async def scenario():
        s, _, _, notes = _session()
        got = []

        async def handler(sheet, transcript, timing):
            got.append((sheet.name, transcript.text))

        s.on_transcript = handler
        await s.talk_start({"npc": NPC})
        await s.talk_end({})
        await _settle()
        assert got == [("Witseidutsei", "Any news from Bravil?")]
        assert notes == []          # the hook decides what to show

    _run(scenario())


def test_key_tap_is_ignored():
    async def scenario():
        s, rec, stt, notes = _session()
        await s.talk_start({"npc": NPC})
        await s.talk_end({})            # immediately: a tap
        await _settle()
        assert rec.stopped == 1 and stt.calls == 0 and notes == []

    _run(scenario())


def test_cancel_discards_recording(monkeypatch):
    monkeypatch.setattr(talk_mod, "MIN_HOLD_SECONDS", 0.0)

    async def scenario():
        s, rec, stt, notes = _session()
        await s.talk_start({"npc": NPC})
        await s.talk_cancel({"reason": "menu opened"})
        await s.talk_end({})            # a stray end after cancel does nothing
        await _settle()
        assert rec.stopped == 1 and stt.calls == 0 and notes == []

    _run(scenario())


def test_nothing_heard(monkeypatch):
    monkeypatch.setattr(talk_mod, "MIN_HOLD_SECONDS", 0.0)

    async def scenario():
        s, _, _, notes = _session(stt=FakeSTT(text=""))
        await s.talk_start({"npc": NPC})
        await s.talk_end({})
        await _settle()
        assert notes == ["Living Cyrodiil: didn't catch that."]

    _run(scenario())


def test_microphone_failure_is_reported():
    async def scenario():
        s, _, stt, notes = _session(recorder=FakeRecorder(fail_start=True))
        await s.talk_start({"npc": NPC})
        await _settle()
        await s.talk_end({})
        await _settle()
        assert any("couldn't open the microphone" in n for n in notes)
        assert stt.calls == 0

    _run(scenario())


def test_talk_start_without_sheet_is_ignored():
    async def scenario():
        s, rec, _, notes = _session()
        await s.talk_start({"type": "talk_start"})
        await s.talk_end({})
        await _settle()
        assert rec.started == 0 and notes == []

    _run(scenario())


def test_release_hook_runs_after_speech_not_after_a_tap(monkeypatch):
    async def scenario():
        s, _, _, _ = _session()
        released = []
        s.on_release = lambda: released.append(1)
        await s.talk_start({"npc": NPC})
        await s.talk_end({})            # a tap: no hook
        await _settle()
        assert released == []
        monkeypatch.setattr(talk_mod, "MIN_HOLD_SECONDS", 0.0)
        await s.talk_start({"npc": NPC})
        await s.talk_end({})
        await _settle()
        assert released == [1]

    _run(scenario())


def test_llm_warm_up_never_raises():
    import time
    from living_cyrodiil.llm import LLMClient
    from living_cyrodiil.settings import LLMSettings

    llm = LLMClient(LLMSettings(api_key="test", base_url="http://127.0.0.1:9/v1", model="test"))   # nothing listens there
    llm.warm_up()
    llm.warm_up()                   # a second call while the first runs is skipped
    for _ in range(100):
        if not llm._warming.locked():
            break
        time.sleep(0.05)
    assert not llm._warming.locked()


def test_typed_line_is_answered_like_a_spoken_one():
    async def scenario():
        s, rec, stt, notes = _session()
        got = []

        async def handler(sheet, transcript, timing):
            got.append((sheet.name, transcript.text, transcript.engine, timing.stt))

        s.on_transcript = handler
        await s.talk_text({"type": "talk_text", "ref_id": "0001", "npc": NPC,
                           "text": "  Any   news from\nBravil? "})
        await s.talk_text({"type": "talk_text", "npc": NPC, "text": "   "})      # nothing typed: ignored
        assert got == [("Witseidutsei", "Any news from Bravil?", "typed", 0.0)]
        assert rec.started == 0 and stt.calls == 0                 # no microphone, no speech-to-text

    _run(scenario())


def test_speaking_off_loads_no_speech_to_text_and_says_so_once():
    async def scenario():
        loads = []

        async def notify(text):
            notes.append(text)
            return True

        notes: list[str] = []
        s = TalkSession(lambda: FakeRecorder(), lambda: loads.append(1) or FakeSTT(), notify, speaking=False)
        await s.talk_start({"npc": NPC})
        await s.talk_end({})
        await s.talk_start({"npc": NPC})
        await _settle()
        assert loads == [] and len(notes) == 1 and "type" in notes[0]
        got = []

        async def handler(sheet, transcript, timing):
            got.append(transcript.text)

        s.on_transcript = handler
        await s.talk_text({"npc": NPC, "text": "Hello there."})
        assert got == ["Hello there."]

    _run(scenario())
