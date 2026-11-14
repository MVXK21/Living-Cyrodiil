"""The lore library (DESIGN.md layer 4, "Scoped knowledge"): short entries in
data/lore/*.toml, each tagged with who would know it and how deep it goes.

For each turn, `LoreLibrary.select` picks the few entries that fit this NPC
(their home, factions and expertise levels) and match what's being talked
about (keywords in the player's line and the NPC's last reply). Entries tagged
with a quest stage are left out until Phase 5 can read quest progress.
See data/lore/README.md for the entry format and the licence (UESP, CC BY-SA).
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import world_events
from .character import CharacterSheet
from .expertise import DEEP, SOME

log = logging.getLogger(__name__)

MAX_ENTRIES = 4         # matched entries per turn
MAX_ALWAYS = 2          # plus up to this many "always" entries (local summaries)


@dataclass(frozen=True)
class LoreEntry:
    id: str
    text: str
    keywords: tuple[str, ...]
    known_by: str = "everyone"      # "everyone", "locals:<place>", "faction:<name>"
    depth: str = "common"           # "common", "specialist", "scholar"
    area: str = ""                  # expertise area that unlocks deeper entries
    always: bool = False
    quest: str = ""                 # set = only known with quest progress (Phase 5)
    # Known only until this happens (Kvatch standing, until KvatchDestroyed).
    # Applied with quest progress (step 3b); until then such entries are known.
    until: str = ""
    # Part of the default story's Oblivion Crisis (the Emperor's murder, the
    # empty throne): left out when the player's world knowledge replaces the
    # world facts (MVXK21, Nov 12: with "the Emperor is alive" on, a guard spoke
    # of the Emperor's murder).
    crisis: bool = False


def _keyword_pattern(word: str) -> re.Pattern:
    return re.compile(rf"(?<![\w']){re.escape(word.lower())}(?![\w'])")


class LoreLibrary:
    def __init__(self, entries: list[LoreEntry]):
        self.entries = entries
        self._patterns = {e.id: [_keyword_pattern(k) for k in e.keywords] for e in entries}

    @staticmethod
    def load(folder: Path) -> "LoreLibrary":
        entries: list[LoreEntry] = []
        seen: set[str] = set()
        for path in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
            for raw in data.get("entry", []):
                e = LoreEntry(
                    id=str(raw["id"]), text=str(raw["text"]).strip(),
                    keywords=tuple(str(k).lower() for k in raw.get("keywords", [])),
                    known_by=str(raw.get("known_by", "everyone")), depth=str(raw.get("depth", "common")),
                    area=str(raw.get("area", "")), always=bool(raw.get("always", False)),
                    quest=str(raw.get("quest", "")), until=str(raw.get("until", "")),
                    crisis=bool(raw.get("crisis", False)),
                )
                if e.id in seen:
                    log.warning("Lore entry id %r appears twice (%s); keeping the first", e.id, path.name)
                    continue
                seen.add(e.id)
                entries.append(e)
        log.info("Lore library: %d entries from %s", len(entries), folder)
        return LoreLibrary(entries)

    # -- who may know what ----------------------------------------------------

    @staticmethod
    def _belongs(entry: LoreEntry, sheet: CharacterSheet) -> bool | None:
        """True if the NPC is in the entry's group, False if not, None for 'everyone'."""
        kind, _, value = entry.known_by.partition(":")
        value = value.strip().lower()
        if kind == "everyone":
            return None
        if kind == "locals":
            return bool(value) and value in sheet.location.lower()
        if kind == "faction":
            return any(f.name.strip().lower() == value for f in sheet.factions)
        return False

    @classmethod
    def may_know(cls, entry: LoreEntry, sheet: CharacterSheet, levels: dict[str, int],
                 own_world: bool = False) -> bool:
        # The player's own version of Cyrodiil (a world knowledge entry that
        # replaces the world facts): no crisis and no quest news, so `crisis`
        # and `quest` entries are out and `until` entries ("Kvatch stands") in.
        if own_world:
            if entry.crisis or entry.quest:
                return False
        else:
            # Quest progress (step 3b): `quest` entries only once it has happened,
            # `until` entries only before. Without a quest report (an older
            # plugin), the world is as it stands at the start of the game.
            state = getattr(sheet, "world_state", None)
            if entry.quest and not world_events.check(entry.quest, state):
                return False
            if entry.until and world_events.check(entry.until, state):
                return False
        belongs = cls._belongs(entry, sheet)
        if belongs is False:
            return False
        level = levels.get(entry.area, 0) if entry.area else 0
        if entry.depth == "common":
            return True
        if entry.depth == "specialist":
            # Their own town's or faction's affairs, or some expertise in the area.
            return bool(belongs) or level >= SOME
        if entry.depth == "scholar":
            return level >= DEEP
        return False

    # -- what's being talked about --------------------------------------------

    def _score(self, entry: LoreEntry, text: str) -> int:
        return sum(1 for p in self._patterns[entry.id] if p.search(text))

    def select(self, sheet: CharacterSheet, levels: dict[str, int], talk: str,
               exclude=None, own_world: bool = False) -> list[LoreEntry]:
        """The entries to give this NPC for this turn. `talk` is the player's line
        plus the NPC's last reply, so follow-up questions stay on topic.
        `exclude(entry) -> bool` leaves entries out, e.g. ones the player's world
        knowledge overrides. `own_world`: a world knowledge entry replaces the
        world facts (see may_know)."""
        text = talk.lower()
        allowed = [e for e in self.entries if self.may_know(e, sheet, levels, own_world)
                   and not (exclude and exclude(e))]
        # Crisis entries split off a local summary don't count towards the cap.
        always = ([e for e in allowed if e.always and not e.crisis][:MAX_ALWAYS]
                  + [e for e in allowed if e.always and e.crisis])
        # Equally good matches: the NPC's own town or district first (lore
        # pass, Nov 10: with every city in the library, "where's an inn?"
        # should get this city's inns before another's).
        scored = sorted(((self._score(e, text), i, e) for i, e in enumerate(allowed) if not e.always),
                        key=lambda t: (-t[0], 0 if self._belongs(t[2], sheet) else 1, t[1]))
        matched = [e for s, _, e in scored if s > 0][:MAX_ENTRIES]
        return always + matched
