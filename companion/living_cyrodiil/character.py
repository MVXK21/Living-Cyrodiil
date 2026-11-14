"""The character sheet: what the game tells us about an NPC at conversation time.

In game, the plugin fills this from live game data (so mod-added NPCs work).
For testing without the game, sample sheets come from data/sample_npcs.toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import DATA_DIR


@dataclass(frozen=True)
class FactionMembership:
    name: str
    rank_title: str = ""      # e.g. "Apprentice"; empty if unknown


@dataclass(frozen=True)
class ChatterLine:
    """A line the NPC just said to another NPC, or heard from them (plugin
    0.18.0, "recent_chatter")."""
    by: str                   # "self" (the NPC said it) or "other" (said to them)
    other_name: str           # the other NPC
    other_ref: str = ""
    seconds_ago: float = 0


# Names a place from a position: (world id hex, x, y) -> places.Whereabouts.
# Set by the companion at startup; None in tests and tools.
place_lookup = None

# Cyrodiil's towns, lower case: an interior named after one is in it.
TOWNS = ("imperial city", "chorrol", "skingrad", "bruma", "anvil", "bravil",
         "cheydinhal", "kvatch", "leyawiin")


@dataclass(frozen=True)
class CharacterSheet:
    name: str
    race: str
    sex: str                  # "male" or "female"
    class_name: str           # e.g. "Publican"
    location: str             # e.g. "a tavern in Leyawiin"
    factions: tuple[FactionMembership, ...] = field(default_factory=tuple)
    disposition: int | None = 50   # toward the player, 0-100; None if the game couldn't say
    hour: float = 12.0        # game hour, 0-24
    interior: bool = True
    # Outdoors inside a walled town or city (its own world in the game, like
    # Leyawiin's streets), as opposed to the open country. Plugin 0.10.2+.
    in_town: bool = False
    notes: str = ""           # test-only extra description; empty in game
    ref_id: str = ""          # game reference id (hex), empty for samples
    plugin: str = ""          # mod file the NPC comes from, e.g. "Oblivion.esm"
    base_id: str = ""         # base NPC record (hex), empty for samples
    # The race whose voice recordings this NPC uses (Dark Elves use High Elf
    # voices). Empty means their own race's.
    voice_race: str = ""
    # Skill levels by name ("Mysticism": 72), from the game. Empty if unknown.
    skills: dict[str, int] = field(default_factory=dict, hash=False, compare=False)
    specialization: str = ""  # class specialization: "combat", "magic" or "stealth"
    major_skills: tuple[str, ...] = ()
    # Phase 5 (plugin 0.16.0): in-game days since the game began, with the
    # hour as a fraction; and the calendar date (month 0 = Morning Star).
    days_passed: float | None = None
    day: int | None = None
    month: int | None = None
    year: int | None = None
    # Phase 5 step 3a (plugin 0.17.0): "clear", "cloudy", "rain", "snow" or ""
    # (unknown), and the player's standing.
    weather: str = ""
    player: "PlayerStanding | None" = None
    # Plugin 0.18.0: what they were just saying to another NPC, oldest first.
    recent_chatter: tuple[ChatterLine, ...] = ()
    # Out in the open country (plugin 0.19.0 positions and the game's map
    # markers): "a few hours' walk north of Chorrol, near Weynon Priory".
    surroundings: str = ""
    # Step 4 (plugin 0.20.0): what they're doing and what they see of the
    # player, raw from the game (awareness.py words them).
    activity: dict = field(default_factory=dict, hash=False, compare=False)
    player_seen: dict = field(default_factory=dict, hash=False, compare=False)
    # Plugin 0.21.5: the NPC's equipped items, [{"name", "kind"}].
    equipment: tuple = field(default=(), hash=False, compare=False)
    # Step 3b (plugin 0.21.0): started quests' stages and set globals (world_events.py).
    world_state: dict = field(default_factory=dict, hash=False, compare=False)

    @staticmethod
    def from_game(npc: dict) -> "CharacterSheet":
        """Build a sheet from the plugin's talk_start message (docs/protocol.md).
        Missing or odd fields fall back to safe defaults rather than failing."""
        factions = tuple(
            FactionMembership(str(f.get("name", "")), str(f.get("rank_title", "")))
            for f in npc.get("factions", [])
            if isinstance(f, dict) and f.get("name") and not f.get("hidden")
        )
        interior = bool(npc.get("interior", False))
        in_town = bool(npc.get("in_town", False)) and not interior
        place = str(npc.get("location") or npc.get("cell") or npc.get("worldspace") or "Cyrodiil")
        outside = str(npc.get("last_outdoor_place") or "")
        # Places with no name of their own in the game data (the Imperial City
        # Waterfront, villages, the open country) are named from the nearest
        # map marker (plugin 0.19.0 sends positions).
        where = None
        if place_lookup is not None:
            try:
                if not interior and not in_town and "pos_x" in npc:
                    where = place_lookup(str(npc.get("world_id", "")), float(npc["pos_x"]), float(npc["pos_y"]))
                elif interior and not outside and "last_outdoor_x" in npc:
                    where = place_lookup(str(npc.get("last_outdoor_world_id", "")),
                                         float(npc["last_outdoor_x"]), float(npc["last_outdoor_y"]))
            except (TypeError, ValueError, KeyError):
                where = None
        surroundings = ""
        if where is not None:
            if interior:
                outside = outside or where.at
            elif where.at:
                place = where.at if place == "Cyrodiil" else (
                    place if where.at.lower() in place.lower() else f"{place}, at {where.at}")
            else:
                surroundings = where.around
        # An interior named after another town ("Bravil Bayside Inn") is in that
        # town, whatever the last outdoor place was (Nov 11 playtest: MVXK21 walked
        # from Leyawiin to the Bravil docks and got "Bravil Bayside Inn, in
        # Leyawiin"; the cause, a stale name in the plugin, is fixed in 0.21.3,
        # and this is a second guard).
        names_a_town = any(t in place.lower() and t not in outside.lower() for t in TOWNS)
        if interior and outside and outside.lower() not in place.lower() and not names_a_town:
            place = f"{place}, in {outside}"
        disposition = npc.get("disposition")
        return CharacterSheet(
            name=str(npc.get("name") or "a stranger"),
            race=str(npc.get("race") or "Imperial"),
            sex=str(npc.get("sex") or "male"),
            class_name=str(npc.get("class") or "Commoner"),
            location=place,
            factions=factions,
            disposition=int(disposition) if isinstance(disposition, (int, float)) else None,
            hour=float(npc.get("hour", 12)),
            interior=interior,
            in_town=in_town,
            ref_id=str(npc.get("ref_id", "")),
            plugin=str(npc.get("plugin", "")),
            base_id=str(npc.get("base_id", "")),
            voice_race=str(npc.get("voice_race", "")),
            skills=_read_skills(npc.get("skills")),
            specialization=str(npc.get("specialization", "")),
            major_skills=tuple(str(s) for s in npc.get("major_skills", []) if isinstance(s, str)),
            days_passed=_number(npc.get("days_passed")),
            day=_whole(npc.get("day")),
            month=_whole(npc.get("month")),
            year=_whole(npc.get("year")),
            weather=str(npc.get("weather") or ""),
            player=PlayerStanding.from_game(npc.get("player")),
            recent_chatter=_read_chatter(npc.get("recent_chatter")),
            surroundings=surroundings,
            activity=npc["activity"] if isinstance(npc.get("activity"), dict) else {},
            player_seen=npc["player_seen"] if isinstance(npc.get("player_seen"), dict) else {},
            equipment=tuple(e for e in npc.get("equipment", ()) if isinstance(e, dict))
            if isinstance(npc.get("equipment"), list) else (),
            world_state=npc["world_state"] if isinstance(npc.get("world_state"), dict) else {},
        )

    @property
    def female(self) -> bool:
        return self.sex.strip().lower() == "female"

    def summary(self) -> str:
        """One line for the companion window."""
        bits = [f"{self.name}: {self.sex} {self.race} {self.class_name.lower()}", self.location]
        if self.factions:
            bits.append(", ".join(f"{f.name} ({f.rank_title})" if f.rank_title else f.name
                                  for f in self.factions))
        bits.append(f"disposition {self.disposition if self.disposition is not None else '?'}")
        bits.append(f"{int(self.hour):02d}:{int((self.hour % 1) * 60):02d}")
        if self.plugin:
            bits.append(f"from {self.plugin}")
        return " | ".join(bits)

    @staticmethod
    def from_dict(d: dict) -> "CharacterSheet":
        factions = tuple(
            FactionMembership(f.get("name", ""), f.get("rank", ""))
            for f in d.get("factions", [])
        )
        return CharacterSheet(
            name=d["name"],
            race=d["race"],
            sex=d["sex"],
            class_name=d.get("class", "Commoner"),
            location=d.get("location", "Cyrodiil"),
            factions=factions,
            disposition=int(d.get("disposition", 50)),
            hour=float(d.get("hour", 12)),
            interior=bool(d.get("interior", True)),
            notes=d.get("notes", ""),
            skills=_read_skills(d.get("skills")),
            specialization=str(d.get("specialization", "")),
            major_skills=tuple(d.get("major_skills", [])),
        )


def _read_skills(raw) -> dict[str, int]:
    """Skill name -> level, ignoring anything that isn't a number."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}


def load_sample_npcs(path: Path = DATA_DIR / "sample_npcs.toml") -> list[CharacterSheet]:
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    return [CharacterSheet.from_dict(d) for d in data.get("npc", [])]


def _read_chatter(data) -> tuple[ChatterLine, ...]:
    if not isinstance(data, list):
        return ()
    out = []
    for d in data:
        if not isinstance(d, dict) or not d.get("other_name"):
            continue
        out.append(ChatterLine(by="self" if d.get("by") == "self" else "other",
                               other_name=str(d["other_name"]), other_ref=str(d.get("other_ref") or ""),
                               seconds_ago=_number(d.get("seconds_ago")) or 0))
    return tuple(out)


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _whole(value) -> int | None:
    n = _number(value)
    return int(n) if n is not None else None


@dataclass(frozen=True)
class PlayerStanding:
    """What the game says about the player: reputation, bounty, guild ranks."""
    fame: int = 0
    infamy: int = 0
    bounty: int = 0
    factions: tuple[FactionMembership, ...] = ()

    @staticmethod
    def from_game(data) -> "PlayerStanding | None":
        if not isinstance(data, dict):
            return None
        factions = tuple(
            FactionMembership(str(f.get("name", "")), str(f.get("rank_title", "")))
            for f in data.get("factions", [])
            if isinstance(f, dict) and f.get("name") and not f.get("hidden")
        )
        return PlayerStanding(fame=_whole(data.get("fame")) or 0, infamy=_whole(data.get("infamy")) or 0,
                              bounty=_whole(data.get("bounty")) or 0, factions=factions)
