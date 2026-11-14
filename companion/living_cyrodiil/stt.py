"""Local speech-to-text. Two engines, compared in Phase 2 step 2:

* Whisper (faster-whisper): accepts a vocabulary hint, so Cyrodiil names
  like "Leyawiin" or "Khajiit" come out spelled right more often.
* Moonshine: built for short live speech, very fast on a CPU.

Both run on the CPU and download their model once into companion/models/.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import numpy as np

from .paths import MODELS_DIR

log = logging.getLogger(__name__)


# Keep Hugging Face downloads inside the project, not a hidden user cache.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Harmless on Windows (files are copied instead of linked); hide the warning.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

MIN_SECONDS = 0.3         # shorter than this is a key tap, not speech


@dataclass(frozen=True)
class Transcript:
    text: str
    seconds: float            # time spent transcribing
    audio_seconds: float
    engine: str


class SpeechToText:
    name = "base"

    def transcribe(self, audio: np.ndarray) -> Transcript:
        audio_seconds = audio.size / 16_000
        if audio_seconds < MIN_SECONDS or float(np.abs(audio).max(initial=0.0)) < 0.01:
            return Transcript("", 0.0, audio_seconds, self.name)
        start = time.perf_counter()
        text = self._transcribe(audio.astype(np.float32))
        result = Transcript(_tidy(text), time.perf_counter() - start, audio_seconds, self.name)
        log.info("STT %s: %.2fs for %.2fs of audio: %r",
                 self.name, result.seconds, audio_seconds, result.text)
        return result

    def _transcribe(self, audio: np.ndarray) -> str:
        raise NotImplementedError


class WhisperEngine(SpeechToText):
    def __init__(self, model: str = "base.en", vocabulary: list[str] | None = None, threads: int = 4):
        from faster_whisper import WhisperModel
        self.name = f"whisper-{model}"
        t = time.perf_counter()
        # Three workers: the final transcription at key release never waits
        # behind the (up to two) started at pauses (live_stt.py).
        self._model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads,
                                   num_workers=3, download_root=str(MODELS_DIR / "whisper"))
        # Whisper treats this as "earlier text", nudging it toward these spellings.
        self._prompt = ("Names from Cyrodiil: " + ", ".join(vocabulary) + ".") if vocabulary else None
        log.info("Loaded %s in %.1fs", self.name, time.perf_counter() - t)

    def _transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=1, vad_filter=False,
            condition_on_previous_text=False, initial_prompt=self._prompt,
        )
        return " ".join(s.text for s in segments)


class MoonshineEngine(SpeechToText):
    def __init__(self, model: str = "base"):
        from moonshine_onnx import MoonshineOnnxModel, load_tokenizer
        self.name = f"moonshine-{model}"
        t = time.perf_counter()
        self._model = MoonshineOnnxModel(model_name=f"moonshine/{model}")
        self._tokenizer = load_tokenizer()
        log.info("Loaded %s in %.1fs", self.name, time.perf_counter() - t)

    def _transcribe(self, audio: np.ndarray) -> str:
        tokens = self._model.generate(audio[None, :])
        return self._tokenizer.decode_batch(tokens)[0]


def make_engine(engine: str, whisper_model: str = "base.en", moonshine_model: str = "base",
                vocabulary: list[str] | None = None) -> SpeechToText:
    if engine == "whisper":
        return WhisperEngine(whisper_model, vocabulary)
    if engine == "moonshine":
        return MoonshineEngine(moonshine_model)
    raise ValueError(f'Unknown speech engine "{engine}" (use "whisper" or "moonshine")')


def _tidy(text: str) -> str:
    t = " ".join(text.split())
    # Whisper sometimes invents these on near-silent clips. ("Thank you" alone
    # is kept: players really say it.)
    if t.lower().strip(" .!") in {"you", "thanks for watching", "thank you for watching"}:
        return ""
    return t
