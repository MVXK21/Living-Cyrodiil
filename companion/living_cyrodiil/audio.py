"""Small audio helpers for Phase 3, using PyAV (already installed for speech
to text): decode the game's MP3s, save WAVs, encode MP3s the game can play.
Audio is passed around as mono float32 numpy arrays in -1..1."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import av
import numpy as np


def decode(data: bytes, sample_rate: int) -> np.ndarray:
    """Any audio file's bytes (MP3, WAV...) -> mono float32 at `sample_rate`."""
    out: list[np.ndarray] = []
    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def encode_mp3(audio: np.ndarray, sample_rate: int, out_rate: int = 44100, bitrate: int = 64000) -> bytes:
    """Mono MP3 like Oblivion's own voice files (44.1 kHz, 64 kbps)."""
    buf = io.BytesIO()
    with av.open(buf, "w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=out_rate, layout="mono")
        stream.bit_rate = bitrate
        resampler = av.AudioResampler(format="s16p", layout="mono", rate=out_rate)
        frame = av.AudioFrame.from_ndarray(
            (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).reshape(1, -1), format="s16p", layout="mono")
        frame.sample_rate = sample_rate
        for f in resampler.resample(frame) + resampler.resample(None):
            for packet in stream.encode(f):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buf.getvalue()


def trim_silence(audio: np.ndarray, sample_rate: int, threshold: float = 0.01, keep: float = 0.05) -> np.ndarray:
    """Cut silence at both ends, keeping `keep` seconds of margin."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return audio
    pad = int(keep * sample_rate)
    return audio[max(0, loud[0] - pad): min(audio.size, loud[-1] + pad)]


def duration(audio: np.ndarray, sample_rate: int) -> float:
    return audio.size / float(sample_rate)


def limit_peaks(audio: np.ndarray, sample_rate: int, ceiling: float = 0.95,
                block_ms: float = 2.5, release_ms: float = 80.0) -> np.ndarray:
    """A peak limiter: turns down only the brief moments that would go over
    `ceiling`, and eases back up over about `release_ms`, so the rest of the
    speech keeps its loudness. Works on short blocks, so it's fast."""
    if audio.size == 0:
        return audio
    block = max(1, int(sample_rate * block_ms / 1000))
    n = -(-audio.size // block)
    padded = np.zeros(n * block, np.float32)
    padded[:audio.size] = np.abs(audio)
    peaks = padded.reshape(n, block).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-9))
    if need.min() >= 1.0:
        return audio
    # Each block also takes its neighbours' gain, so the gain is already down
    # when a peak starts (blending between blocks can't let a peak through).
    need = np.minimum(need, np.minimum(np.r_[need[1:], 1.0], np.r_[1.0, need[:-1]]))
    recover = 1.0 - np.exp(-block_ms / release_ms)
    gain = np.empty(n, np.float64)
    g = 1.0
    for i in range(n):
        g = min(need[i], g + (1.0 - g) * recover)
        gain[i] = g
    centres = (np.arange(n) + 0.5) * block
    per_sample = np.interp(np.arange(audio.size), centres, gain)
    return np.clip(audio * per_sample, -ceiling, ceiling).astype(np.float32)
