"""Splits an NPC reply into subtitle chunks (Phase 2 step 6d).

The game shows a reply as up to four subtitles, one after another, like a
vanilla line with several responses. Each chunk is whole sentences where
possible, at most about MAX_CHARS long (vanilla responses run up to ~150
characters). A sentence that's too long on its own is split at a comma,
semicolon or dash, or failing that between words.
"""

from __future__ import annotations

import re

MAX_CHARS = 150
MAX_CHUNKS = 4     # must match the plugin (speak.h kMaxChunks) and the .esp

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+")
_SOFT_BREAK = re.compile(r"(?<=[,;:—–])\s+|\s+(?=[—–-]\s)")


def _split_long(sentence: str, limit: int) -> list[str]:
    """Break one over-long sentence at soft breaks, then between words."""
    parts: list[str] = []
    current = ""
    for piece in _SOFT_BREAK.split(sentence):
        piece = piece.strip()
        if not piece:
            continue
        candidate = f"{current} {piece}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(piece) <= limit:
            current = piece
            continue
        # No soft break short enough: split between words.
        current = ""
        for word in piece.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) <= limit or not current:
                current = candidate
            else:
                parts.append(current)
                current = word
    if current:
        parts.append(current)
    return parts


def _pack(text: str, limit: int) -> list[str]:
    sentences: list[str] = []
    for s in _SENTENCE_END.split(text):
        s = s.strip()
        if s:
            sentences.extend(_split_long(s, limit) if len(s) > limit else [s])
    chunks: list[str] = []
    for s in sentences:
        if chunks and len(chunks[-1]) + 1 + len(s) <= limit:
            chunks[-1] = f"{chunks[-1]} {s}"
        else:
            chunks.append(s)
    return chunks


def split_into_chunks(text: str, max_chars: int = MAX_CHARS, max_chunks: int = MAX_CHUNKS) -> list[str]:
    """Return 1 to max_chunks subtitle chunks for `text` (empty list if blank)."""
    text = " ".join(text.split())
    if not text:
        return []
    limit = max_chars
    chunks = _pack(text, limit)
    # Too many chunks: allow longer ones rather than dropping words.
    while len(chunks) > max_chunks:
        limit = int(limit * 1.25) + 1
        chunks = _pack(text, limit)
    return chunks
