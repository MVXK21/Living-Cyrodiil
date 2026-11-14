"""Microphone recording for push-to-talk.

start() begins capturing, stop() returns the audio as 16 kHz mono float32,
which is what both speech-to-text engines expect.

keep_open: Bluetooth headsets switch to a low-quality "hands-free" mode
whenever the microphone is open, which also makes game audio sound worse.
  * keep_open=False (default): the mic opens only while you talk. Game audio
    stays good, but a Bluetooth headset may take a moment to switch, so the
    first word can get cut off.
  * keep_open=True: the mic stays open, so nothing is cut off, but on a
    Bluetooth headset game audio stays in hands-free quality the whole time.
Wired and USB mics don't have this problem either way.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
MAX_SECONDS = 30          # safety cap on a single recording


def list_input_devices() -> list[tuple[int, str]]:
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME":
            devices.append((i, d["name"]))
    return devices


def resample(audio: np.ndarray, from_rate: int, to_rate: int = TARGET_RATE) -> np.ndarray:
    if from_rate == to_rate or audio.size == 0:
        return audio.astype(np.float32)
    import soxr  # installed with the Moonshine dependencies
    return soxr.resample(audio, from_rate, to_rate).astype(np.float32)


class Recorder:
    def __init__(self, device: int | str | None = None, keep_open: bool = False):
        self.device = device if device not in ("", None) else None
        self.keep_open = keep_open
        info = sd.query_devices(self.device, kind="input")
        self.device_name = info["name"]
        self.rate = int(info["default_samplerate"])
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._started_at = 0.0
        log.info("Microphone: %s (%d Hz, keep_open=%s)", self.device_name, self.rate, keep_open)
        if keep_open:
            self._open()

    # -- stream handling -----------------------------------------------------

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("Mic status: %s", status)
        if self._recording:
            with self._lock:
                self._chunks.append(indata[:, 0].copy())

    def _open(self) -> None:
        if self._stream is None:
            t = time.perf_counter()
            self._stream = sd.InputStream(device=self.device, channels=1, samplerate=self.rate,
                                          dtype="float32", callback=self._callback)
            self._stream.start()
            log.debug("Mic opened in %.2fs", time.perf_counter() - t)

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # -- push-to-talk ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        self._recording = True
        self._started_at = time.perf_counter()
        self._open()

    def snapshot(self) -> np.ndarray:
        """The audio so far, as 16 kHz mono float32, without stopping."""
        with self._lock:
            raw = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        return resample(raw[: self.rate * MAX_SECONDS], self.rate)

    def stop(self) -> np.ndarray:
        """Stop recording and return 16 kHz mono float32 audio."""
        self._recording = False
        held = time.perf_counter() - self._started_at
        if not self.keep_open:
            self._close()
        with self._lock:
            raw = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        raw = raw[: self.rate * MAX_SECONDS]
        audio = resample(raw, self.rate)
        log.info("Recorded %.2fs of audio (key held %.2fs, peak level %.2f)",
                 audio.size / TARGET_RATE, held, float(np.abs(audio).max()) if audio.size else 0.0)
        return audio

    def close(self) -> None:
        self._recording = False
        self._close()


def save_wav(path, audio: np.ndarray, rate: int = TARGET_RATE) -> None:
    """Save float32 audio as a 16-bit WAV (for replaying test clips)."""
    import wave
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def load_wav(path) -> np.ndarray:
    """Load a WAV file as 16 kHz mono float32."""
    import wave
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit WAV files are supported")
    audio = np.frombuffer(data, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    return resample(audio, rate)
