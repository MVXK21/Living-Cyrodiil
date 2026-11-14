"""Speech to text while the talk key is held (Phase 3 step 4b-3).

Whisper takes about 0.6 s whatever the length of the speech (measured Nov 7:
a fitted window doesn't help short phrases), so transcribing piece by piece
wouldn't save much. Instead, whenever the player has been quiet for
PAUSE_SECONDS, everything said so far is transcribed in the background. At
key release, if nothing was said after that point, that text is used at once
(players let go 0.4-1.0 s after their last word in the test clips). If they
spoke again, the whole recording is transcribed as before.

Up to two background transcriptions run at once, so one started at a
mid-sentence pause doesn't hold up the one for the last pause. The engine
has three workers (WhisperEngine num_workers=3), so the final transcription
never waits behind them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .stt import Transcript

log = logging.getLogger(__name__)

RATE = 16_000
FRAME = 320                    # 20 ms
PAUSE_SECONDS = 0.2            # quiet this long after speech: transcribe what's been said
MAX_BACKGROUND = 2             # background transcriptions at once
POLL_SECONDS = 0.05


def last_speech_end(audio: np.ndarray) -> int:
    """Sample index just after the last frame that holds speech (0 = none).

    Speech is a frame louder than the mic's own noise: over an absolute
    floor, over 5% of the loudest frame, and over 3x the quiet frames. Set
    low on purpose: a quiet last word counted as speech only costs a fresh
    transcription, while one missed would drop it from the text."""
    n = audio.size // FRAME
    if n == 0:
        return 0
    rms = np.sqrt(np.mean(audio[: n * FRAME].reshape(n, FRAME) ** 2, axis=1))
    threshold = max(0.006, 0.05 * float(rms.max()), 3.0 * float(np.percentile(rms, 10)))
    loud = np.flatnonzero(rms > threshold)
    return int(loud[-1] + 1) * FRAME if loud.size else 0


@dataclass
class _Job:
    cut: int                                   # samples transcribed: audio[:cut]
    task: asyncio.Task


class LiveTranscriber:
    """One per held key. feed() gets the audio so far while the key is down;
    finish() gets the whole recording at release and returns its transcript."""

    def __init__(self, transcribe: Callable[[np.ndarray], Transcript], pause_seconds: float = PAUSE_SECONDS):
        self._transcribe = transcribe
        self._pause = int(pause_seconds * RATE)
        self._job: _Job | None = None          # newest background transcription
        self._running: list[asyncio.Task] = []
        self.jobs_started = 0

    def feed(self, audio: np.ndarray) -> None:
        end = last_speech_end(audio)
        if end == 0 or audio.size - end < self._pause:
            return                              # no speech yet, or still talking
        if self._job and self._job.cut >= end:
            return                              # this pause is already covered
        self._running = [t for t in self._running if not t.done()]
        if len(self._running) >= MAX_BACKGROUND:
            return                              # busy; the next poll catches up
        clip = audio.copy()
        self.jobs_started += 1
        log.debug("Live STT: pause after %.2fs of audio; transcribing it in the background", end / RATE)
        task = asyncio.create_task(asyncio.to_thread(self._transcribe, clip))
        self._running.append(task)
        self._job = _Job(clip.size, task)

    async def finish(self, audio: np.ndarray) -> tuple[Transcript, str]:
        """The transcript of the whole recording, and how it was got (for
        the log). Reuses the background transcription when no speech came
        after it."""
        start = time.perf_counter()
        end = last_speech_end(audio)
        job = self._job
        if job and job.cut >= end:
            try:
                early = await job.task
            except Exception:
                log.exception("Background speech-to-text failed; transcribing again")
            else:
                waited = time.perf_counter() - start
                how = (f"ready at release ({early.seconds:.2f}s, done while the key was held)" if waited < 0.01
                       else f"finished {waited:.2f}s after release (started at the last pause)")
                return Transcript(early.text, waited, audio.size / RATE, early.engine), how
        self.cancel()
        why = "no pause before release" if job is None else "speech after the last pause"
        full = await asyncio.to_thread(self._transcribe, audio)
        return full, f"transcribed at release ({why})"

    def cancel(self) -> None:
        """Forget the background job (its result, when it comes, is unused)."""
        self._job = None
