"""Phase 3 step 3: voiced replies, with a stand-in voice (no Pocket TTS needed)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np

from living_cyrodiil import audio
from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.game_chat import reply_message
from living_cyrodiil.settings import VoiceSettings
from living_cyrodiil.voice_out import SPEECH_RMS, ReplyVoice, even_out
from living_cyrodiil.voices import VoiceChoice

RATE = 24000


def _tone(seconds: float, level: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_even_out_trims_and_sets_loudness():
    quiet = np.concatenate([np.zeros(RATE), _tone(1.0, 0.02), np.zeros(RATE)])
    out = even_out(quiet, RATE)
    assert 0.9 < out.size / RATE < 1.3                       # silence trimmed (small margins kept)
    assert abs(float(np.sqrt(np.mean(out ** 2))) - SPEECH_RMS) < 0.01
    assert float(np.abs(out).max()) <= 0.95


def test_loud_peaks_are_limited_but_speech_keeps_its_loudness():
    from living_cyrodiil.audio import limit_peaks
    speech = _tone(2.0, 0.1)                      # RMS about 0.07, so even_out turns it up 1.5x
    speech[RATE: RATE + 100] = 0.9                # a short spike well over the limit at this level...
    out = even_out(speech, RATE)
    assert float(np.abs(out).max()) <= 0.95       # ...limited
    rms = float(np.sqrt(np.mean(out ** 2)))
    assert abs(rms - SPEECH_RMS) < 0.01           # the rest isn't turned down
    quiet = _tone(0.5, 0.3)
    assert limit_peaks(quiet, RATE) is quiet      # nothing over the limit: unchanged


def test_reply_message():
    assert reply_message("0001", "Hi.", ["Hi."], voiced=False) == {
        "type": "reply", "ref_id": "0001", "text": "Hi.", "chunks": ["Hi."], "listener": "player"}
    assert reply_message("0001", "Hi.", ["Hi."], voiced=True)["voiced"] is True


class _FakeBank:
    def __init__(self, fail_on: str = ""):
        self.fail_on = fail_on
        self.said: list[str] = []

    def say(self, choice, text):
        if text == self.fail_on:
            raise RuntimeError("no speech today")
        self.said.append(text)
        return _tone(0.5 + len(text) / 30, 0.3)


class _FakeLibrary:
    def choose(self, **kw):
        return VoiceChoice("oblivion.esm_argonian_f", "Argonian female", Path("ref.wav"))


def _voice(tmp_path, bank) -> ReplyVoice:
    v = ReplyVoice(VoiceSettings(), audio_dir=tmp_path)
    v.library, v.bank = _FakeLibrary(), bank
    return v


SHEET = CharacterSheet(name="Witseidutsei", race="Argonian", sex="female", class_name="Publican",
                       location="Leyawiin", ref_id="0002C8D9", base_id="0002C8D8")


def test_speak_sends_one_audio_message_per_part_in_order(tmp_path):
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)
        return True

    voice = _voice(tmp_path, _FakeBank())
    assert voice.ready
    asyncio.run(voice.speak(SHEET, ["Welcome, friend.", "The stew is hot."], send))
    assert [m["part"] for m in sent] == [1, 2]
    for m in sent:
        assert m["type"] == "reply_audio" and m["ref_id"] == "0002C8D9" and m["turn"] == 1
        path = Path(m["path"])
        assert path.exists() and path.parent == tmp_path
        decoded = audio.decode(path.read_bytes(), RATE)       # a real MP3 the game can read
        assert abs(audio.duration(decoded, RATE) - m["seconds"]) < 0.15


def test_failed_part_sends_error_and_others_still_play(tmp_path):
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)
        return True

    voice = _voice(tmp_path, _FakeBank(fail_on="Bad part."))
    asyncio.run(voice.speak(SHEET, ["Good part.", "Bad part.", "Also good."], send))
    assert [m["part"] for m in sent] == [1, 2, 3]
    assert "error" in sent[1] and "path" not in sent[1]
    assert "path" in sent[0] and "path" in sent[2]


def test_not_ready_until_loaded(tmp_path):
    assert not ReplyVoice(VoiceSettings(), audio_dir=tmp_path).ready


def test_each_part_gets_a_lip_file_unless_lip_sync_is_off(tmp_path):
    from living_cyrodiil import lip
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)
        return True

    voice = _voice(tmp_path, _FakeBank())
    asyncio.run(voice.speak(SHEET, ["Welcome, friend."], send))
    lip_path = Path(sent[0]["lip"])
    assert lip_path == Path(sent[0]["path"]).with_suffix(".lip")
    weights, offset = lip.decode(lip_path.read_bytes())
    assert offset == 0 and abs(len(weights) / lip.FPS - sent[0]["seconds"]) < 0.05

    sent.clear()
    off = ReplyVoice(VoiceSettings(lip_sync="off"), audio_dir=tmp_path)
    off.library, off.bank = _FakeLibrary(), _FakeBank()
    asyncio.run(off.speak(SHEET, ["Welcome, friend."], send, turn=1))   # same file names as before
    assert sent[0]["lip"] == "" and not lip_path.exists()             # the old one is gone
