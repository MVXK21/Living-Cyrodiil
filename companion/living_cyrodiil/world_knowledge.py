"""Player-written world knowledge (Phase 2 step 7b; DESIGN.md, "World knowledge").

An optional plain-text file the player edits in Notepad: numbered entries,
each switched on or off, describing how their world differs from the default.

    [on, replace] 1: The Empire is at peace, the Emperor is alive, ...
    [off] 2: A plague has struck Bravil.

* An enabled entry is a true fact about the world, full stop: it wins every
  conflict with the lore library, the default world facts and the LLM's own
  knowledge (MVXK21, Nov 4, 2026).
* A "replace" entry stands in for the setting profile's default world facts
  while it's on; a plain entry adds to them.
* Lore library entries on the same subject are left out while an entry is on,
  so the model never sees both versions. Disabled entries aren't used at all.
* The file is re-read whenever it changes, so edits apply without a restart.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

TEMPLATE = """\
# Living Cyrodiil: world knowledge
#
# Optional. Describe how YOUR Cyrodiil differs from the default, in plain
# sentences. NPCs treat every entry that's switched on as the truth of their
# world, above everything else they know. Switch an entry off and they go back
# to the usual lore.
#
# One entry per line:
#   [on] 1: what's true          an entry that adds to the usual world facts
#   [on, replace] 1: ...         an entry that replaces the usual world facts
#                                (use it when it contradicts them, like a living Emperor)
#   [off] 1: ...                 switched off: NPCs ignore it
# A long entry can continue on the next lines if they start with spaces.
# Lines starting with # are notes and are ignored. Save the file and the
# companion picks up the change straight away.

[off, replace] 1: The Empire is at peace, the Emperor is alive, the Mythic Dawn is unknown, no Oblivion gates have opened, Kvatch still stands, and the Oblivion Crisis has not happened yet.
[off] 2: Cyrodiil is a province with a tropical climate and largely jungled. The Nibenay region is built into a large rainforest. The climate is mostly hot and humid, though County Bruma is much colder and generally not tropical.
"""

_ENTRY = re.compile(r"^\[\s*(on|off)\s*(?:,\s*(replace|add)\s*)?\]\s*(?:(\d+)\s*[:.)-]\s*)?(.*)$", re.I)

# Lore keywords too general to say "this entry is about the same subject".
_GENERIC = {
    "city", "town", "here", "news", "history", "old", "ancient", "guild", "guilds", "people",
    "land", "work", "job", "jobs", "trade", "book", "books", "map", "maps", "level", "levels",
    "learn", "power", "politics", "government", "war", "wars", "order", "symbol", "statue",
    "spell", "spells", "magic", "school", "schools", "food", "eat", "drink", "room", "bed",
}


@dataclass(frozen=True)
class Entry:
    number: int
    text: str
    enabled: bool
    replace: bool


def parse(text: str) -> list[Entry]:
    """Entries from the file's text. Unrecognised lines are logged and skipped."""
    entries: list[Entry] = []
    pending: dict | None = None

    def flush():
        if pending and pending["text"].strip():
            entries.append(Entry(pending["number"], " ".join(pending["text"].split()),
                                 pending["enabled"], pending["replace"]))

    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() and pending is not None and not line.lstrip().startswith("["):
            pending["text"] += " " + line.strip()          # continuation line
            continue
        m = _ENTRY.match(line.strip())
        if not m:
            log.warning("World knowledge line %d not understood (start it with [on] or [off]): %r", i, line)
            continue
        flush()
        state, mode, number, body = m.groups()
        pending = {"number": int(number) if number else len(entries) + 1, "text": body,
                   "enabled": state.lower() == "on", "replace": (mode or "").lower() == "replace"}
    flush()
    return entries


def knowledge_path(settings) -> Path:
    """Where the player's file lives: [world] knowledge_file, relative to the
    companion folder unless it's a full path."""
    from .paths import USER_DIR
    p = Path(settings.knowledge_file)
    return p if p.is_absolute() else USER_DIR / p


class WorldKnowledge:
    """The player's world knowledge file, re-read whenever it changes."""

    def __init__(self, path: Path, create: bool = True):
        self.path = path
        self._mtime: float | None = None
        self._entries: list[Entry] = []
        if create and not path.exists():
            try:
                path.write_text(TEMPLATE, encoding="utf-8")
                log.info("Created %s (both example entries are off)", path)
            except OSError as e:
                log.warning("Couldn't create %s: %s", path, e)

    def _refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._entries, self._mtime = [], None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._entries = parse(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as e:
            log.warning("Couldn't read %s: %s", self.path, e)
            self._entries = []
        on = [e for e in self._entries if e.enabled]
        log.info("World knowledge: %d entr%s, %d on%s", len(self._entries),
                 "y" if len(self._entries) == 1 else "ies", len(on),
                 (": " + ", ".join(str(e.number) for e in on)) if on else "")

    def entries(self) -> list[Entry]:
        self._refresh()
        return list(self._entries)

    def active(self) -> list[Entry]:
        return [e for e in self.entries() if e.enabled]

    def replaces_world_facts(self) -> bool:
        return any(e.replace for e in self.active())

    def covers(self, keywords: tuple[str, ...], active: list[Entry] | None = None) -> bool:
        """True if an enabled entry is about the same subject as these lore
        keywords, so the lore entry is left out: the entry shares at least two
        specific keywords with it. One shared word isn't enough ("the Emperor is
        alive" must not hide who the Emperor is; lore bench, Nov 5, 2026: NPCs
        then named the wrong Emperor)."""
        active = self.active() if active is None else active
        if not active:
            return False
        for entry in active:
            text = entry.text.lower()
            hits = {k for k in keywords
                    if k not in _GENERIC and len(k) >= 4
                    and re.search(rf"(?<![\w']){re.escape(k)}(?![\w'])", text)}
            if len(hits) >= 2:
                return True
        return False
