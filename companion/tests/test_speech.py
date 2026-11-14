"""Offline tests for speech settings, audio helpers and transcript tidying.
They don't load speech models or open the microphone."""

from __future__ import annotations

import numpy as np
import pytest

from living_cyrodiil.recorder import load_wav, resample, save_wav
from living_cyrodiil.settings import SettingsError, load_settings
from living_cyrodiil.setting_profile import load_profile
from living_cyrodiil.stt import SpeechToText, _tidy


def test_resample_44k_to_16k_keeps_duration():
    one_second = np.sin(np.linspace(0, 440 * 2 * np.pi, 44_100)).astype(np.float32)
    out = resample(one_second, 44_100)
    assert out.dtype == np.float32
    assert abs(out.size - 16_000) <= 2


def test_wav_round_trip(tmp_path):
    audio = (np.sin(np.linspace(0, 200 * 2 * np.pi, 16_000)) * 0.5).astype(np.float32)
    path = tmp_path / "clip.wav"
    save_wav(path, audio)
    back = load_wav(path)
    assert back.size == audio.size
    assert np.max(np.abs(back - audio)) < 1e-3


def test_tidy_drops_known_phantoms_but_keeps_thank_you():
    assert _tidy("  Thanks for watching! ") == ""
    assert _tidy(" you. ") == ""
    assert _tidy("Thank you.") == "Thank you."
    assert _tidy("  Any   news? ") == "Any news?"


class _Echo(SpeechToText):
    name = "echo"

    def _transcribe(self, audio):
        return "  hello   there "


def test_short_or_silent_clips_skip_the_engine():
    eng = _Echo()
    assert eng.transcribe(np.zeros(16_000, np.float32)).text == ""         # silence
    assert eng.transcribe(np.full(1_000, 0.5, np.float32)).text == ""      # key tap
    assert eng.transcribe(np.full(16_000, 0.5, np.float32)).text == "hello there"


def test_vocabulary_has_lore_names():
    vocab = load_profile().stt_vocabulary
    assert {"Leyawiin", "Khajiit", "Septim"} <= set(vocab)


def _settings(tmp_path, speech: str):
    p = tmp_path / "s.toml"
    p.write_text('[llm]\napi_key = "x"\n' + speech, encoding="utf-8")
    return load_settings(p)


def test_speech_settings_defaults_and_mic_number(tmp_path):
    s = _settings(tmp_path, "")
    assert s.speech.engine == "whisper" and s.speech.mic_device is None
    s = _settings(tmp_path, '[speech]\nengine = "Moonshine"\nmic_device = "3"\nkeep_mic_open = true\n')
    assert s.speech.engine == "moonshine" and s.speech.mic_device == 3 and s.speech.keep_mic_open


def test_speech_settings_reject_unknown_engine(tmp_path):
    with pytest.raises(SettingsError, match="engine"):
        _settings(tmp_path, '[speech]\nengine = "dragon"\n')
