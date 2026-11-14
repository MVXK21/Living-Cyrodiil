"""The world right now, and the player's standing, in plain words for the
prompt (Phase 5 step 3a, docs/phase5-plan.md). Decisions by MVXK21, Nov 9, 2026:

* Guild ranks: fellow members of the same guild know the player's rank;
  everyone knows the public-facing ones (Arch-Mage, Fighters Guild Master,
  Arena Champion and Grand Champion; the setting file's `public_ranks`).
* Bounty: guards always know it; other people only once it's 1,000 gold or
  more (when the game's own guards attack on sight).
* Fame and infamy come across as rumours, by level, not as numbers.
"""

from __future__ import annotations

from .character import CharacterSheet
from .setting_profile import SettingProfile

WANTED_BOUNTY = 1000                 # everyone hears of a bounty this big
REPUTATION_LEVELS = (10, 30, 60)     # fame or infamy: rumours, well known, famous


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def describe_date(sheet: CharacterSheet, profile: SettingProfile) -> str:
    """"Today is the 27th of Last Seed." plus how people say it ("" if unknown).
    MVXK21 (Nov 10 playtest): NPCs always added "year 433 of the Third Era",
    which nobody says in passing; the day and month are enough. Nov 11
    playtest of 0.21.2: asked about the weather, Sylvia added the date as
    small talk; so the date is only for when it's asked for."""
    names = profile.month_names
    if sheet.day is None or sheet.month is None or not (0 <= sheet.month < len(names)):
        return ""
    date = f"the {_ordinal(sheet.day)} of {names[sheet.month]}"
    return (f"Today is {date}. Don't mention the date unless the traveller asks for it or it truly "
            f"matters to what you're saying. If you do, say it as people do, simply \"{date}\"; "
            "don't add the year or the era unless someone asks for them.")


def describe_weather(sheet: CharacterSheet) -> str:
    words = {"clear": "the weather is fair", "cloudy": "the sky is overcast",
             "rain": "it's raining", "snow": "it's snowing"}.get(sheet.weather, "")
    if not words:
        return ""
    if sheet.interior:
        return f"Outside, {words}."
    return words[0].upper() + words[1:] + "."


def is_guard(sheet: CharacterSheet) -> bool:
    """City guards, the Watch and the Legion: by class or faction name."""
    names = [sheet.class_name] + [f.name for f in sheet.factions]
    return any(w in n.lower() for n in names for w in ("guard", "watch", "legion"))


def _level(value: int) -> int:
    return sum(value >= t for t in REPUTATION_LEVELS)


FAME = ("",
        "You've heard some good talk about this traveller's deeds.",
        "This traveller is well spoken of for their good deeds, and you recognise them as the one people talk about.",
        "This traveller is famous for their great deeds; you recognise them at once as the one everyone talks about.")
INFAMY = ("",
          "There are whispers that this traveller isn't to be trusted.",
          "This traveller has a bad name: people say they're a thief, or worse.",
          "This traveller is notorious; people speak of them with fear.")


def describe_standing(sheet: CharacterSheet, profile: SettingProfile) -> list[str]:
    """Sentences about the player that this NPC would know."""
    p = sheet.player
    if p is None:
        return []
    lines: list[str] = []
    own = {f.name.lower() for f in sheet.factions}
    public = profile.public_ranks or {}
    for f in p.factions:
        name = f.name.lower()
        title = f.rank_title.lower()
        if name in own:
            rank = f" (rank: {f.rank_title})" if f.rank_title else ""
            lines.append(f"The traveller is a fellow member of the {f.name}{rank}.")
        elif title and title in public.get(name, ()):
            lines.append(f"Everyone knows the traveller is the {f.rank_title} of the {f.name}.")
    if p.bounty > 0 and is_guard(sheet):
        lines.append(f"As a guard, you know the traveller has a bounty of {p.bounty} gold on their head.")
    elif p.bounty >= WANTED_BOUNTY:
        lines.append("Word is that the traveller is a wanted criminal with a large price on their head.")
    if FAME[_level(p.fame)]:
        lines.append(FAME[_level(p.fame)])
    if INFAMY[_level(p.infamy)]:
        lines.append(INFAMY[_level(p.infamy)])
    return lines
