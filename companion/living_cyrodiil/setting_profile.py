"""A setting profile: the world facts, ground rules and race speech notes the
prompt uses. Lives in an editable file (data/setting_<name>.toml) so lore can
be tuned, or swapped for the old-lore jungle profile, without touching code.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .expertise import ExpertiseRules
from .bios import Bios
from .lore import LoreLibrary
from .paths import DATA_DIR


@dataclass(frozen=True)
class SettingProfile:
    name: str
    world_facts: tuple[str, ...]
    ground_rules: tuple[str, ...]
    race_notes: dict[str, str]          # lower-case race name -> note
    banned_terms: tuple[str, ...]       # replies containing these are retried
    fallback_lines: tuple[str, ...]     # in-character "I don't follow" lines
    stt_vocabulary: tuple[str, ...] = ()  # names that help speech-to-text spell lore words
    # lower-case race name -> words only that race uses (banned for everyone else)
    race_only_terms: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    expertise: ExpertiseRules = ExpertiseRules()        # how much NPCs know about each subject
    lore: LoreLibrary = None  # type: ignore[assignment]  # the lore library (data/lore/)
    bios: Bios = None  # type: ignore[assignment]         # key characters' bios (data/bios/)
    # Phase 5 step 3a: the calendar's month names (month 0 first), and guild
    # ranks everyone knows (lower-case faction name -> lower-case rank titles).
    month_names: tuple[str, ...] = ()
    public_ranks: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]

    def race_note(self, race: str) -> str:
        """Exact race name first; otherwise a known race contained in the name,
        so mod variants like "Khajiit Cathay" or "Imperial Colovian" still get
        their race's note. Longest match wins ("dark elf" before "elf")."""
        known = _best_race_match(race, self.race_notes)
        return self.race_notes[known] if known else ""

    def banned_for(self, race: str) -> tuple[str, ...]:
        """The banned terms for an NPC of this race: the shared list, plus every
        other race's own words (a Dark Elf may say "fetcher"; a Nord may not)."""
        own = _best_race_match(race, self.race_only_terms or {})
        extra = [t for r, terms in (self.race_only_terms or {}).items() if r != own for t in terms]
        return self.banned_terms + tuple(extra)


def _best_race_match(race: str, table: dict) -> str | None:
    """Same matching as race_note: exact, else the longest known race contained
    in the name ("Dark Elf (Vampire)" -> "dark elf")."""
    key = race.strip().lower()
    if key in table:
        return key
    for known in sorted(table, key=len, reverse=True):
        if known in key:
            return known
    return None


def load_profile(name: str = "vanilla", data_dir: Path = DATA_DIR) -> SettingProfile:
    path = data_dir / f"setting_{name}.toml"
    d = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    return SettingProfile(
        name=d.get("name", name),
        world_facts=tuple(d.get("world_facts", [])),
        ground_rules=tuple(d.get("ground_rules", [])),
        race_notes={k.lower(): v for k, v in d.get("race_notes", {}).items()},
        banned_terms=tuple(d.get("banned_terms", [])),
        fallback_lines=tuple(d.get("fallback_lines", ["Hm? I don't follow you."])),
        stt_vocabulary=tuple(d.get("stt_vocabulary", [])),
        race_only_terms={k.lower(): tuple(v) for k, v in d.get("race_only_terms", {}).items()},
        expertise=ExpertiseRules.from_dict(d.get("expertise")),
        lore=LoreLibrary.load(data_dir / str(d.get("lore_folder", "lore"))),
        bios=Bios.load(data_dir / str(d.get("bios_folder", "bios"))),
        month_names=tuple(d.get("month_names", [])),
        public_ranks={k.lower(): tuple(t.lower() for t in v) for k, v in d.get("public_ranks", {}).items()},
    )
