"""Lip-sync from the voice alone (Phase 4 step 2).

A small network learns the game's own lip files: it reads the player's
vanilla voice lines with their .lip files (the same voice actors our voices
are cloned from) and learns which mouth shapes go with which sounds. It's
built once on the player's machine, the first time the companion runs, and
saved in companion/cache/lip/; nothing derived from the game's files is
shipped. After that, a line's lips take a few milliseconds.

What it sees: 40-band log-mel audio features, 30 frames a second (one per
lip frame). What it gives: the 16 mouth-shape weights per frame. Blinks,
brows, glances and head movement are borrowed from a vanilla line of about
the same length (see borrow_gestures()); blinks() is the stand-in when no
gesture bank could be built.

Research and the first probe: docs/lipsync-research.md.
"""

from __future__ import annotations

import logging
import random
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio, lip
from .paths import CACHE_DIR as USER_CACHE

log = logging.getLogger(__name__)

CACHE_DIR = USER_CACHE / "lip"
MODEL_FILE = CACHE_DIR / "lip_model.pt"
GESTURE_FILE = CACHE_DIR / "gestures.npz"
GESTURE_VERSION = 1
GESTURE_LINES = 2000
FIRST_GESTURE = lip.CHANNELS["BlinkLeft"]      # channels 16-32: blinks, brows, eye direction, head
VERSION = 2                      # bump when features, network or training change: rebuilds the model
                                 # (2, Nov 7: 30,000 lines x 12 passes, from 15,000 x 10)

RATE = 16_000
HOP = RATE // lip.FPS            # one feature frame per lip frame
WIN = 1024                       # 64 ms
MELS = 40
SHAPES = len(lip.MOUTH_SHAPES)   # 16
MAX_WEIGHT = 1.4                 # vanilla mouth weights reach about 1.3

Progress = Callable[[str], None]


# ---- audio features ------------------------------------------------------------

def _mel_bank() -> np.ndarray:
    def hz2mel(h): return 2595 * np.log10(1 + h / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    pts = mel2hz(np.linspace(hz2mel(60), hz2mel(7600), MELS + 2))
    bins = np.floor((WIN + 1) * pts / RATE).astype(int)
    fb = np.zeros((MELS, WIN // 2 + 1))
    for i in range(1, MELS + 1):
        l, c, r = bins[i - 1], bins[i], bins[i + 1]
        fb[i - 1, l:c] = (np.arange(l, c) - l) / max(1, c - l)
        fb[i - 1, c:r] = (r - np.arange(c, r)) / max(1, r - c)
    return fb.astype(np.float32)


_FB = _mel_bank()
_WINDOW = np.hanning(WIN).astype(np.float32)
_FLOOR = np.float32(np.log(1e-6))


def features(wav16k: np.ndarray, frames: int) -> np.ndarray:
    """frames x MELS log-mel features, frame t centred on t / 30 s. The
    loudness is evened out first, so quiet and loud lines look alike."""
    x = wav16k.astype(np.float32)
    rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
    if rms > 1e-6:
        x = x * (0.1 / rms)
    pad = np.pad(x, (WIN // 2, WIN + frames * HOP))
    idx = np.arange(frames)[:, None] * HOP + np.arange(WIN)[None, :]
    spec = np.abs(np.fft.rfft(pad[idx] * _WINDOW, axis=1)) ** 2
    return np.log(spec.astype(np.float32) @ _FB.T + 1e-6).astype(np.float32)


# ---- training data: the player's own vanilla lines ----------------------------------

@dataclass
class Example:
    feats: np.ndarray      # frames x MELS
    shapes: np.ndarray     # frames x 16, on the audio's own timeline


def _example(archive, lip_path: str) -> Example | None:
    try:
        weights, offset = lip.decode(archive.read(lip_path))
    except Exception:
        return None                                   # not a lip file this code can read
    n = len(weights)
    if n < 15:
        return None
    shapes = weights[:, :SHAPES]
    # Lip frame t goes with audio frame t + offset (-15 to 0 in vanilla, one file +28):
    # drop the first -offset frames to put the lips on the audio's timeline.
    s = -offset
    if 0 < s < n:
        shapes = np.concatenate([shapes[s:], np.zeros((s, SHAPES), np.float32)])
    try:
        wav = audio.decode(archive.read(lip_path[:-4] + ".mp3"), RATE)
    except Exception:
        return None
    if wav.size < RATE // 4:
        return None
    return Example(features(wav, n), shapes.astype(np.float32))


_worker_archives: list = []


def _worker_start(paths: list[str]) -> None:
    from .bsa import BsaArchive
    global _worker_archives
    _worker_archives = [BsaArchive(Path(p)) for p in paths]


def _worker_example(job: tuple[int, str]) -> Example | None:
    return _example(_worker_archives[job[0]], job[1])


def collect(archives: list, max_lines: int, seed: int = 1, progress: Progress | None = None,
            workers: int | None = None) -> list[Example]:
    """Up to `max_lines` (audio, lips) examples from the voice archives, a
    fair mix of every voice type, the same picks every time. Decoding the
    MP3s is most of the work (about 12 ms a line), so it runs in worker
    processes, one per spare CPU core; threads if those can't start."""
    import os
    jobs = []
    for i, a in enumerate(archives):
        files = set(a.files())
        jobs += [(i, p) for p in sorted(files) if p.endswith(".lip") and p[:-4] + ".mp3" in files]
    random.Random(seed).shuffle(jobs)
    jobs = jobs[:max_lines]
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 2))
    out: list[Example] = []

    def gather(results) -> None:
        for done, ex in enumerate(results, start=1):
            if ex is not None:
                out.append(ex)
            if progress and done % 2000 == 0:
                progress(f"read {done} of {len(jobs)} lines")

    try:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers, initializer=_worker_start,
                                 initargs=([str(a.path) for a in archives],)) as pool:
            gather(pool.map(_worker_example, jobs, chunksize=32))
    except Exception as e:
        log.warning("Lip model: worker processes failed (%s); reading with threads instead", e)
        out.clear()
        with ThreadPoolExecutor(workers) as pool:
            gather(pool.map(lambda j: _example(archives[j[0]], j[1]), jobs, chunksize=16))
    log.info("Lip model: %d usable examples from %d lines (%d workers)", len(out), len(jobs), workers)
    return out


# ---- the network ----------------------------------------------------------------------

def _torch():
    import torch
    return torch


def make_net():
    """A stack of 1-D convolutions along time: it sees about 0.45 s either
    side of each frame, enough for the mouth to lead the sound as vanilla's
    do (by about 0.1 s)."""
    torch = _torch()
    nn = torch.nn

    class Block(nn.Module):
        def __init__(self, ch, dilation):
            super().__init__()
            self.conv = nn.Conv1d(ch, ch, 5, padding=2 * dilation, dilation=dilation)
            self.norm = nn.BatchNorm1d(ch)

        def forward(self, x):
            return x + torch.nn.functional.gelu(self.norm(self.conv(x)))

    class LipNet(nn.Module):
        def __init__(self, ch=128):
            super().__init__()
            self.inp = nn.Conv1d(MELS, ch, 3, padding=1)
            self.blocks = nn.Sequential(*[Block(ch, d) for d in (1, 2, 4, 1)])
            self.out = nn.Conv1d(ch, SHAPES, 1)

        def forward(self, x):                      # x: batch x MELS x frames
            h = torch.nn.functional.gelu(self.inp(x))
            return MAX_WEIGHT * torch.sigmoid(self.out(self.blocks(h)))

    return LipNet()


@dataclass
class TrainResult:
    net: object
    mean: np.ndarray
    std: np.ndarray
    seconds: float


def train(examples: list[Example], epochs: int = 6, crop: int = 150, batch: int = 64,
          progress: Progress | None = None, seed: int = 1, threads: int = 4) -> TrainResult:
    torch = _torch()
    # Pocket TTS sets torch to one thread (its fastest); training wants more.
    was = torch.get_num_threads()
    torch.set_num_threads(threads)
    try:
        return _train(torch, examples, epochs, crop, batch, progress, seed)
    finally:
        torch.set_num_threads(was)


def _train(torch, examples, epochs, crop, batch, progress, seed) -> TrainResult:
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    allf = np.concatenate([e.feats for e in examples])
    mean, std = allf.mean(0), allf.std(0) + 1e-5
    del allf
    feats = [(e.feats - mean) / std for e in examples]
    shapes = [e.shapes for e in examples]
    net = make_net()
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-4)
    steps_per_epoch = max(1, sum(len(f) for f in feats) // (crop * batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 2e-3, total_steps=epochs * steps_per_epoch)
    start = time.perf_counter()
    fill = (_FLOOR - mean) / std
    for epoch in range(epochs):
        net.train()
        total = 0.0
        for _ in range(steps_per_epoch):
            xb = np.empty((batch, MELS, crop), np.float32)
            yb = np.zeros((batch, SHAPES, crop), np.float32)
            for b in range(batch):
                i = int(rng.integers(len(feats)))
                f, y = feats[i], shapes[i]
                if len(f) > crop:
                    s = int(rng.integers(len(f) - crop + 1))
                    xb[b], yb[b] = f[s:s + crop].T, y[s:s + crop].T
                else:
                    xb[b] = fill[:, None]
                    xb[b, :, :len(f)], yb[b, :, :len(f)] = f.T, y.T
            pred = net(torch.from_numpy(xb))
            loss = torch.nn.functional.mse_loss(pred, torch.from_numpy(yb))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            opt.step()
            sched.step()
            total += loss.item()
        msg = f"training pass {epoch + 1} of {epochs}: error {total / steps_per_epoch:.4f}"
        log.info("Lip model: %s", msg)
        if progress:
            progress(msg)
    net.eval()
    return TrainResult(net, mean.astype(np.float32), std.astype(np.float32), time.perf_counter() - start)


def predict(net, mean: np.ndarray, std: np.ndarray, feats: np.ndarray) -> np.ndarray:
    """frames x 16 mouth weights."""
    torch = _torch()
    x = torch.from_numpy(((feats - mean) / std).T[None].astype(np.float32))
    with torch.no_grad():
        return net(x)[0].numpy().T.copy()


def evaluate(result: TrainResult, examples: list[Example]) -> dict:
    """How close the model's lips come to the game's own, on lines it didn't train on."""
    P = np.concatenate([predict(result.net, result.mean, result.std, e.feats) for e in examples])
    Y = np.concatenate([e.shapes for e in examples])

    def corr_of(a, b):
        return float(np.corrcoef(a, b)[0, 1]) if a.std() > 1e-6 and b.std() > 1e-6 else 0.0

    corr = [corr_of(P[:, i], Y[:, i]) for i in range(SHAPES)]
    talking = Y.max(1) > 0.3
    vowels = [lip.CHANNELS[n] for n in ("Aah", "BigAah", "Eee", "Eh", "I", "Oh", "OohQ")]
    return {
        "mse": float(np.mean((P - Y) ** 2)),
        "per_shape": dict(zip(lip.MOUTH_SHAPES, corr)),
        "mean_corr": float(np.mean(corr)),
        "main_shape_agrees": float(np.mean(P[talking].argmax(1) == Y[talking].argmax(1))),
        "opening_corr": corr_of(P[:, vowels].sum(1), Y[:, vowels].sum(1)),
    }


# ---- blinks and the stand-in ------------------------------------------------------------

def blinks(frames: int, seed: int) -> np.ndarray:
    """Blink weights (both eyes) for a line: vanilla lines blink every 1-3
    seconds, a quick close and a slightly slower open (about 0.2 s)."""
    rng = random.Random(seed)
    out = np.zeros(frames, np.float32)
    shape = np.array([0.5, 1.0, 1.0, 0.65, 0.3], np.float32)
    t = rng.randint(8, 40)
    while t < frames:
        end = min(frames, t + len(shape))
        out[t:end] = np.maximum(out[t:end], shape[:end - t])
        t += rng.randint(30, 90)
    return out


def loudness_shapes(wav16k: np.ndarray, frames: int) -> np.ndarray:
    """The stand-in until the model is built: the jaw opens with the
    loudness of the speech (Aah), leading the sound by 0.1 s as real
    mouths do. Rough, but the face isn't frozen."""
    x = np.pad(wav16k.astype(np.float32), (0, frames * HOP))[: frames * HOP]
    env = np.sqrt(np.mean(x.reshape(frames, HOP) ** 2, axis=1))
    peak = float(np.percentile(env, 95)) if env.size else 0.0
    out = np.zeros((frames, SHAPES), np.float32)
    if peak <= 1e-6:
        return out
    open_ = np.clip(env / peak, 0.0, 1.0) * 0.8
    open_ = np.convolve(open_, np.ones(3) / 3, mode="same")
    lead = 3
    open_ = np.concatenate([open_[lead:], np.zeros(lead, np.float32)])
    out[:, lip.CHANNELS["Aah"]] = open_
    return out


# ---- gestures: borrowed from vanilla lines -------------------------------------------
# Brows, glances and head movement follow the voice only loosely (Nov 7:
# correlation about 0.3 with loudness), so instead of a model each part
# borrows the gesture track of a vanilla line of about the same length,
# stretched to fit. Blinks come with it, so they stay in step with the
# glances as in vanilla.

def build_gestures(archives: list, lines: int = GESTURE_LINES, seed: int = 2) -> list[np.ndarray]:
    """Gesture tracks (frames x 17, channels 16-32) from vanilla lip files:
    lines that do have gestures, the same picks every time."""
    paths = [(a, p) for a in archives for p in sorted(a.files()) if p.endswith(".lip")]
    random.Random(seed).shuffle(paths)
    out = []
    for a, p in paths:
        if len(out) >= lines:
            break
        try:
            weights, offset = lip.decode(a.read(p))
        except Exception:
            continue
        s = -offset
        if 0 < s < len(weights):
            weights = weights[s:]
        g = weights[:, FIRST_GESTURE:]
        if len(g) >= 30 and g[:, 6:].max() > 0.05:          # brows, look or head actually move
            out.append(g.astype(np.float16))
    return out


def borrow_gestures(bank: list[np.ndarray], frames: int, seed: int) -> np.ndarray:
    """frames x 17 gestures from a bank line of about the same length (one
    of the 8 closest, picked by `seed`), stretched or squeezed to fit."""
    lengths = np.array([len(g) for g in bank])
    near = np.argsort(np.abs(lengths - frames))[:8]
    g = bank[int(near[random.Random(seed).randrange(len(near))])].astype(np.float32)
    src = np.linspace(0, len(g) - 1, frames)
    out = np.stack([np.interp(src, np.arange(len(g)), g[:, c]) for c in range(g.shape[1])], axis=1)
    return out.astype(np.float32)


# ---- at play time ---------------------------------------------------------------------------

class LipSync:
    """Makes each spoken part's lip weights. The model loads from the cache,
    or (first run) is built in the background; until then, the loudness
    stand-in is used."""

    def __init__(self, data_folder: Path | None, max_lines: int = 30_000, epochs: int = 12,
                 model_file: Path = MODEL_FILE):
        self.data_folder = data_folder
        self.max_lines = max_lines
        self.epochs = epochs
        self.model_file = model_file
        self._net = None
        self._mean = self._std = None
        self._gestures: list[np.ndarray] = []
        self.gesture_file = model_file.with_name(GESTURE_FILE.name)
        self.status = "not started"

    @property
    def ready(self) -> bool:
        return self._net is not None

    def load_or_build(self, progress: Progress | None = None) -> None:
        """Blocking: run it in a worker thread."""
        try:
            self._load_gestures()
        except Exception:
            log.exception("Lip gestures: couldn't load or build; blinks only")
        try:
            if self._load():
                self.status = "ready"
                return
            self._build(progress or (lambda m: None))
        except Exception as e:
            log.exception("Lip model: couldn't load or build; mouths follow the loudness instead")
            self.status = f"failed ({e})"

    def _load_gestures(self) -> None:
        if self.gesture_file.is_file():
            saved = np.load(self.gesture_file, allow_pickle=False)
            if int(saved["version"]) == GESTURE_VERSION:
                lengths = saved["lengths"]
                flat = saved["tracks"]
                cuts = np.cumsum(lengths)[:-1]
                self._gestures = list(np.split(flat, cuts))
                log.info("Lip gestures loaded: %d vanilla tracks", len(self._gestures))
                return
        if self.data_folder is None:
            return
        from .bsa import BsaArchive
        from .game_files import voice_archives
        t = time.perf_counter()
        archives = [BsaArchive(p) for p in voice_archives(self.data_folder)]
        bank = build_gestures(archives)
        if not bank:
            return
        self.gesture_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.gesture_file.with_name("gestures.tmp.npz")
        np.savez_compressed(tmp, version=GESTURE_VERSION, lengths=np.array([len(g) for g in bank]),
                            tracks=np.concatenate(bank))
        tmp.replace(self.gesture_file)
        self._gestures = bank
        log.info("Lip gestures built: %d vanilla tracks in %.1fs", len(bank), time.perf_counter() - t)

    def _load(self) -> bool:
        if not self.model_file.is_file():
            return False
        torch = _torch()
        saved = torch.load(self.model_file, map_location="cpu", weights_only=False)
        if saved.get("version") != VERSION:
            log.info("Lip model: the saved one is version %s, this is %s; building again", saved.get("version"), VERSION)
            return False
        net = make_net()
        net.load_state_dict(saved["net"])
        net.eval()
        self._mean, self._std, self._net = saved["mean"], saved["std"], net
        log.info("Lip model loaded (%s)", saved.get("note", ""))
        return True

    def _build(self, progress: Progress) -> None:
        from .bsa import BsaArchive
        from .game_files import voice_archives
        if self.data_folder is None:
            raise RuntimeError("Oblivion's Data folder wasn't found")
        archives = [BsaArchive(p) for p in voice_archives(self.data_folder)]
        if not archives:
            raise RuntimeError("no voice archives in the Data folder")
        t = time.perf_counter()
        self.status = "reading the game's lip files"
        progress("Lip-sync: learning from your game's own lip files (first run only, a few minutes)...")
        examples = collect(archives, self.max_lines, progress=lambda m: progress(f"Lip-sync: {m}"))
        if len(examples) < 500:
            raise RuntimeError(f"only {len(examples)} usable lines")
        test, examples = examples[:300], examples[300:]
        self.status = "training"
        result = train(examples, epochs=self.epochs, progress=lambda m: progress(f"Lip-sync: {m}"))
        score = evaluate(result, test)
        note = (f"{len(examples)} lines, {time.perf_counter() - t:.0f}s to build; on unseen lines: "
                f"mean correlation {score['mean_corr']:.2f}, main shape agrees {score['main_shape_agrees']:.0%}")
        log.info("Lip model built: %s", note)
        torch = _torch()
        self.model_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.model_file.with_suffix(".tmp")
        torch.save({"version": VERSION, "net": result.net.state_dict(), "mean": result.mean,
                    "std": result.std, "note": note, "score": score}, tmp)
        tmp.replace(self.model_file)
        self._mean, self._std, self._net = result.mean, result.std, result.net
        self.status = "ready"
        progress(f"Lip-sync ready ({time.perf_counter() - t:.0f}s).")

    def weights(self, speech: np.ndarray, sample_rate: int, seed: int = 0) -> np.ndarray:
        """frames x 33 lip weights for one spoken part (30 frames a second)."""
        from .recorder import resample
        wav = resample(speech, sample_rate, RATE)
        frames = lip.frames_for(wav.size / RATE)
        if self._net is not None:
            shapes = predict(self._net, self._mean, self._std, features(wav, frames))
        else:
            shapes = loudness_shapes(wav, frames)
        shapes[shapes < 0.02] = 0.0                    # vanilla files hold exact zeros between shapes
        out = np.zeros((frames, lip.WEIGHTS), np.float32)
        out[:, :SHAPES] = shapes
        if self._gestures:
            out[:, FIRST_GESTURE:] = borrow_gestures(self._gestures, frames, seed)
        else:
            b = blinks(frames, seed)
            out[:, lip.CHANNELS["BlinkLeft"]] = b
            out[:, lip.CHANNELS["BlinkRight"]] = b
        return out
