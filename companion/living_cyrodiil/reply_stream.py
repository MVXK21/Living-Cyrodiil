"""Sentence-by-sentence replies (Phase 3 step 4b).

The LLM's reply streams in a few words at a time. SentenceSplitter hands
back each sentence as soon as it's complete, so the first one can be checked,
voiced and sent to the game while the rest is still being written.

A sentence ends at . ! ? or an ellipsis, followed by any closing quotes or
brackets, then a space. It never ends inside a stage direction (*...*,
(...) or [...]), so "*He sets down his mug. He sighs.*" stays whole and the
cleaner can remove it in one piece.

FirstPart decides when part 1 is ready: the first good sentence, unless it's
very short ("Ah."), in which case the next sentence joins it when both fit
in one subtitle. A lone short subtitle followed by a pause sounds choppy.
"""

from __future__ import annotations

from .subtitles import MAX_CHARS

SHORT_FIRST_SENTENCE = 25      # characters: shorter than this, wait for the next one
_ENDS = ".!?…"
_CLOSERS = "\"')]”’"
_OPEN_CLOSE = {"(": ")", "[": "]"}


class SentenceSplitter:
    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        """Adds streamed text; returns the sentences it completed (raw)."""
        self._buf += text
        out: list[str] = []
        while True:
            end = self._find_end(self._buf)
            if end is None:
                break
            sentence, self._buf = self._buf[:end].strip(), self._buf[end:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> str:
        """Whatever is left once the reply has finished (may be a fragment)."""
        rest, self._buf = self._buf.strip(), ""
        return rest

    @staticmethod
    def _find_end(buf: str) -> int | None:
        """Index just after the first complete sentence and its space, or None."""
        stars = 0
        closing: list[str] = []
        i, n = 0, len(buf)
        while i < n:
            c = buf[i]
            if c == "*":
                stars += 1
            elif c in _OPEN_CLOSE:
                closing.append(_OPEN_CLOSE[c])
            elif closing and c == closing[-1]:
                closing.pop()
            elif c in _ENDS and stars % 2 == 0 and not closing:
                j = i + 1
                while j < n and (buf[j] in _ENDS or buf[j] in _CLOSERS):
                    j += 1
                if j < n and buf[j].isspace():
                    return j + 1
                if j >= n:
                    return None        # need the next piece to know it's the end
                i = j
                continue
            i += 1
        return None


def first_part_ready(sentences: list[str], finished: bool) -> str | None:
    """Part 1's text once it's decided, else None (wait for more).

    `sentences` are the good, cleaned sentences so far; `finished` means the
    reply is complete (no more sentences coming)."""
    if not sentences:
        return None
    first = sentences[0]
    if len(first) >= SHORT_FIRST_SENTENCE:
        return first
    if len(sentences) >= 2:
        joined = f"{first} {sentences[1]}"
        return joined if len(joined) <= MAX_CHARS else first
    return first if finished else None
