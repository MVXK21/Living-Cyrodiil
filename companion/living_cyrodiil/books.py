"""The game's own books as knowledge (Phase 5 step 5c). Read from the player's
own Oblivion.esm at first run and cached (never shipped).

MVXK21 (Nov 7): everyone knows the gist of popular books; scholars and the right
specialists know the rest. "Popular" is judged from the game itself: how many
copies are placed around the world. Books that appear nowhere (quest items,
often spoilers), scrolls, short notes and test books are left out.

When the player names a book, the NPC who'd know it gets its opening, as a
reminder of what it says: a short one for common knowledge, a longer one for
NPCs with expertise in its subject.
"""

from __future__ import annotations

import html
import json
import logging
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import esm
from .expertise import SOME
from .paths import CACHE_DIR

log = logging.getLogger(__name__)

CACHE = CACHE_DIR / "books.json"
MIN_TEXT = 1500            # shorter than this is a note or a letter, not a book
POPULAR_COPIES = 60        # this many copies around Cyrodiil: everyone knows the gist
COMMON_CHARS = 400
EXPERT_CHARS = 1400
MAGIC_WORDS = ("spell", "magic", "magicka", "alchemy", "daedra", "daedric", "mage", "mages", "conjur", "enchant",
               "necromanc", "sorcer", "mystic", "illusion", "destruction", "alteration", "restoration")

# The library the prompt uses, set by the companion at startup (None in tests and tools).
library: "BookLibrary | None" = None


@dataclass(frozen=True)
class Book:
    title: str
    text: str
    copies: int

    @property
    def popular(self) -> bool:
        return self.copies >= POPULAR_COPIES

    @property
    def area(self) -> str:
        t = (self.title + " " + self.text[:2000]).lower()
        return "magic" if sum(t.count(w) for w in MAGIC_WORDS) >= 3 else "history"


def clean(raw: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>|</?p[^>]*>|</?div[^>]*>", " ", raw)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def scan(path: Path) -> list[Book]:
    data = path.read_bytes()
    (header,) = struct.unpack_from("<I", data, 4)
    raw: dict[int, tuple[str, str, int]] = {}
    copies: Counter = Counter()
    for tag, fid, body in esm._records(data, 20 + header, len(data), {b"BOOK", b"REFR"}):
        sd = dict(esm._subrecords(body))
        if tag == b"BOOK":
            raw[fid] = (sd.get(b"FULL", b"").rstrip(b"\0").decode("cp1252", "replace"),
                        sd.get(b"DESC", b"").rstrip(b"\0").decode("cp1252", "replace"), sd.get(b"DATA", b"\0")[0])
        elif len(sd.get(b"NAME", b"")) >= 4:
            copies[struct.unpack("<I", sd[b"NAME"][:4])[0]] += 1
    books = []
    for fid, (title, desc, flags) in raw.items():
        text = clean(desc)
        if flags & 1 or not title or len(text) < MIN_TEXT or not copies[fid] or "test" in title.lower():
            continue
        books.append(Book(title, text, copies[fid]))
    return books


def load(data_folder: Path | None) -> list[Book]:
    path = data_folder / "Oblivion.esm" if data_folder else None
    if path is None or not path.is_file():
        return []
    stamp = f"{path.stat().st_size}-{int(path.stat().st_mtime)}"
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return [Book(*b) for b in cached["books"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    books = scan(path)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"stamp": stamp, "books": [[b.title, b.text, b.copies] for b in books]}), encoding="utf-8")
    log.info("Books: %d read from Oblivion.esm (%d popular)", len(books), sum(b.popular for b in books))
    return books


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    s = re.sub(r"\b(the|a|an|of|on|v|vol|volume|book)\b", " ", s)
    return " ".join(s.split())


class BookLibrary:
    def __init__(self, books: list[Book]):
        self.books = books
        self._keys = [(_norm(b.title), b) for b in books]
        # A series by its title alone ("Brief History of the Empire") means its
        # first volume.
        series: dict[str, Book] = {}
        for key, b in sorted(self._keys, key=lambda t: t[0]):
            base = re.sub(r"( \d+)+$", "", key)
            if base != key and base not in series:
                series[base] = b
        self._keys += [(k, b) for k, b in series.items() if all(k != key for key, _ in self._keys)]

    def mentioned(self, talk: str) -> list[Book]:
        """Books named in what's being talked about (the whole title, give or
        take articles and volume words), longest titles first."""
        text = f" {_norm(talk)} "
        hits, seen = [], set()
        for key, b in sorted(self._keys, key=lambda t: -len(t[0])):
            if len(key) >= 4 and f" {key} " in text and b.title not in seen:
                hits.append(b)
                seen.add(b.title)
        return hits

    def note(self, levels: dict[str, int], talk: str) -> str:
        """The prompt's note on a book the player named, if this NPC would know
        it ("" otherwise)."""
        for b in self.mentioned(talk):
            expert = levels.get(b.area, 0) >= SOME
            if not (b.popular or expert):
                continue
            n = EXPERT_CHARS if expert else COMMON_CHARS
            excerpt = b.text[:n].rsplit(" ", 1)[0] + "..."
            how = "You've read it closely" if expert else "It's a well-known book; you know its gist"
            return (f"The book \"{b.title}\": {how}. It begins: \"{excerpt}\" Speak of it in your own words, "
                    "as someone who has read it, not by quoting it.")
        return ""
