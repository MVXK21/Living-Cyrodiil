"""How much an NPC knows about each subject (Phase 2 step 7, "Lore depth by NPC").

Each knowledge area in the setting profile's [expertise] table gets a level for
this NPC: DEEP, SOME or NONE, from their skills (read live from the game),
their factions and their class. The prompt gets a short note saying what they
know well and what is beyond them, and the lore library uses the levels to
decide which entries an NPC may draw on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .character import CharacterSheet

NONE, SOME, DEEP = 0, 1, 2


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    skills: tuple[str, ...] = ()
    factions: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    lacking_note: bool = False


@dataclass(frozen=True)
class ExpertiseRules:
    deep: int = 60
    some: int = 35
    areas: tuple[Area, ...] = ()

    @staticmethod
    def from_dict(d: dict | None) -> "ExpertiseRules":
        d = d or {}
        areas = tuple(
            Area(key=k, label=str(a.get("label", k)), skills=tuple(a.get("skills", [])),
                 factions=tuple(a.get("factions", [])), classes=tuple(a.get("classes", [])),
                 lacking_note=bool(a.get("lacking_note", False)))
            for k, a in (d.get("areas") or {}).items()
        )
        return ExpertiseRules(deep=int(d.get("deep", 60)), some=int(d.get("some", 35)), areas=areas)


def _lower_set(items) -> set[str]:
    return {str(i).strip().lower() for i in items}


def levels(sheet: CharacterSheet, rules: ExpertiseRules) -> dict[str, int]:
    """Area key -> NONE, SOME or DEEP for this NPC."""
    skills = {k.lower(): v for k, v in sheet.skills.items()}
    majors = _lower_set(sheet.major_skills)
    factions = _lower_set(f.name for f in sheet.factions)
    cls = sheet.class_name.strip().lower()
    out: dict[str, int] = {}

    def skill_level(name: str) -> int:
        v = skills.get(name.lower(), 0)
        if v >= rules.deep:
            return DEEP
        if v >= rules.some:
            # Vanilla NPCs' skills run low: Leyawiin's smith Tun-Zeeus has Armorer
            # 38 (Nov 5, 2026). A class major skill at this level is their trade.
            return DEEP if name.lower() in majors else SOME
        return NONE

    for area in rules.areas:
        level = max((skill_level(s) for s in area.skills), default=NONE)
        in_faction = bool(factions & _lower_set(area.factions))
        has_class = cls in _lower_set(area.classes)
        if in_faction and has_class:
            level = max(level, DEEP)
        elif in_faction or has_class:
            level = max(level, SOME)
        out[area.key] = level
    return out


def _join(labels: list[str]) -> str:
    """"a, b and c"; with semicolons when the labels have commas or "and" of
    their own ("trade, prices and haggling; and persuading people")."""
    if len(labels) == 1:
        return labels[0]
    if any("," in l or " and " in l for l in labels):
        return "; ".join(labels[:-1]) + "; and " + labels[-1]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def expertise_note(sheet: CharacterSheet, rules: ExpertiseRules) -> str:
    """A few sentences for the prompt, or "" if nothing is known about the NPC
    (no skills, no matching faction or class)."""
    if not rules.areas:
        return ""
    lv = levels(sheet, rules)
    if not sheet.skills and not any(lv.values()):
        return ""      # nothing to go on: leave it to the model
    by_key = {a.key: a for a in rules.areas}
    deep = [by_key[k].label for k, v in lv.items() if v == DEEP]
    some = [by_key[k].label for k, v in lv.items() if v == SOME]
    lacking = [by_key[k].label for k, v in lv.items() if v == NONE and by_key[k].lacking_note]
    parts = []
    if deep:
        parts.append(f"You know {_join(deep)} deeply; that is your own trade or study, "
                     "and you can speak of it in detail.")
    if some:
        parts.append(f"You know something of {_join(some)}, as a practised amateur would.")
    if lacking:
        parts.append(f"{_join(lacking)[0].upper() + _join(lacking)[1:]} "
                     f"{'is' if len(lacking) == 1 else 'are'} beyond you: you know only what "
                     "common folk say. If asked, say so plainly in your own way instead of "
                     "explaining; never give a learned or detailed answer about them.")
    return " ".join(parts)
