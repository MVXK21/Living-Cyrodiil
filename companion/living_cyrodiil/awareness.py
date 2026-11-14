"""NPC awareness, part 1 (Phase 5 step 4): what the NPC was doing when the
player came up, and what they can see of the player, as plain facts for the
prompt. The plugin (0.20.0) reads them with the game's own condition
functions; the LLM picks a reaction that fits the character (DESIGN.md, NPC
awareness: a guard stern at a drawn sword, a farmer frightened).

MVXK21 (Nov 7): acknowledging that they're reading matters; the book's title
doesn't (the player can't see or check it).
"""

from __future__ import annotations

EAT, SLEEP, WANDER, TRAVEL = 3, 4, 5, 6        # the game's AI package types (xOBSE GameForms.h)


def _n(d: dict, key: str) -> float:
    v = d.get(key, -1) if isinstance(d, dict) else -1
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else -1.0


def describe_activity(activity: dict) -> str:
    """"You were reading when the traveller came up." ("" if nothing to say)."""
    if not isinstance(activity, dict) or not activity:
        return ""
    if _n(activity, "sleeping") > 0:
        return "You were asleep, and the traveller has just woken you."
    doing = []
    if activity.get("reading"):
        doing.append("reading a book")
    elif isinstance(activity.get("using"), str) and activity["using"]:
        doing.append(f"busy with {activity['using'].lower()}")
    package = _n(activity, "package")
    if package == EAT:
        doing.append("having a meal")
    sitting = _n(activity, "sitting") > 0
    if not doing:
        if package == SLEEP:
            doing.append("about to turn in for the night")
        elif package == TRAVEL:
            doing.append("on your way somewhere")
        elif package == WANDER and not sitting:
            doing.append("passing the time")
    if sitting:
        doing.insert(0, "sitting down")
    if not doing:
        return ""
    what = doing[0] if len(doing) == 1 else ", ".join(doing[:-1]) + " and " + doing[-1]
    return f"When the traveller came up to you, you were {what}."


def describe_player(seen: dict) -> list[str]:
    """What the NPC can see of the player, as plain facts ([] if nothing notable)."""
    if not isinstance(seen, dict) or not seen:
        return []
    facts = []
    # Plugin 0.21.6 (MVXK21, Nov 11: NPCs should know everything about the
    # player that anyone seeing them would): race, sex and their gear.
    sex = seen.get("sex") if seen.get("sex") in ("male", "female") else ""
    race = seen.get("race") if isinstance(seen.get("race"), str) else ""
    if sex or race:
        who = {"male": "man", "female": "woman"}.get(sex, "person")
        facts.append(f"The traveller is {'a ' + race + ' ' + who if race else 'a ' + who}"
                     f"{' (so: he, him)' if sex == 'male' else ' (so: she, her)' if sex == 'female' else ''}.")
    parts, weapons, by_kind = _gear(seen.get("equipment"))
    if parts:
        # No example gear in the wording: "steel armour and a mace" was taken
        # as a fact (Nov 11 playtest: Nahsi said the leather-clad player wore
        # steel armour).
        facts.append("The traveller has on: " + "; ".join(parts) + ". (If you speak of it, do so "
                     "as anyone would at a glance, never as a list of item names.)")
    if _n(seen, "sneaking") > 0:
        facts.append("The traveller was creeping up on you, sneaking, which is odd behaviour.")
    if _n(seen, "weapon_out") > 0:
        facts.append(f"The traveller has their {weapons[0]} drawn." if weapons else "The traveller has a weapon drawn.")
    if _n(seen, "in_combat") > 0:
        facts.append("The traveller is in the middle of a fight.")
    health = _n(seen, "health_pct")
    if 0 <= health < 35:
        facts.append("The traveller is badly hurt and bleeding.")
    elif 0 <= health < 70:
        facts.append("The traveller looks hurt.")
    armor, clothes = _n(seen, "armor_rating"), _n(seen, "clothing_value")
    worn = by_kind.keys() & {"heavy armor", "light armor", "clothing"}
    if armor >= 60 and not worn:
        facts.append("The traveller is in heavy armour.")
    elif armor >= 15 and not worn:
        facts.append("The traveller is wearing armour.")
    elif armor == 0 and 0 <= clothes < 5 and "clothing" not in by_kind:
        facts.append("The traveller is barely dressed, in nothing but their smallclothes.")
    elif clothes >= 1000:
        facts.append("The traveller is dressed in fine, costly clothes.")
    return facts


WEAPONS = ("one-handed blade", "two-handed blade", "one-handed blunt weapon",
           "two-handed blunt weapon", "staff", "bow", "weapon")
GEAR_ORDER = WEAPONS + ("arrows", "shield", "heavy armor", "light armor", "clothing")
GEAR_WORDS = {"heavy armor": "heavy armour", "light armor": "light armour"}


def _gear(equipment) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Equipped items grouped by kind: (["bow: Steel Bow", ...], weapon names, by kind)."""
    by_kind: dict[str, list[str]] = {}
    for item in equipment if isinstance(equipment, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        kind, name = str(item.get("kind", "")), str(item.get("name", "")).strip()
        if kind in GEAR_ORDER and name and name not in by_kind.get(kind, []):
            by_kind.setdefault(kind, []).append(name)
    parts = [f"{GEAR_WORDS.get(k, k)}: {', '.join(by_kind[k])}" for k in GEAR_ORDER if k in by_kind]
    weapons = [n for k in WEAPONS for n in by_kind.get(k, [])]
    return parts, weapons, by_kind


def describe_equipment(equipment, weapon_out: float = -1) -> str:
    """What the NPC has on them, from the plugin's equipped items (0.21.5).
    MVXK21 (Nov 11 playtest): an Argonian with a bow on his back denied having
    one; NPCs knew nothing of their own gear. "" if nothing is known."""
    parts, weapons, _ = _gear(equipment)
    if not parts:
        return ""
    text = ("What you have on you, which you know as well as anyone knows their own gear "
            "(never deny it, though you needn't mention it; if you do, speak of it as people do, "
            "not by its full item name): " + "; ".join(parts) + ".")
    if weapon_out > 0 and weapons:
        text += f" You have your {weapons[0]} drawn."
    return text


def armour_weight(by_kind: dict) -> str:
    """"heavy armour", "light armour", "a mix of heavy and light armour" or "no armour"."""
    heavy, light = "heavy armor" in by_kind, "light armor" in by_kind
    return ("a mix of heavy and light armour" if heavy and light else "heavy armour" if heavy
            else "light armour" if light else "no armour")


def compare_armour(npc_equipment, player_equipment) -> str:
    """One plain line on who wears what weight of armour, so the two lists
    can't be mixed up (Nov 11 playtest: Nahsi, in light armour, told the
    player in leather that *he* wore the heavy armour). "" unless both known."""
    _, _, mine = _gear(npc_equipment)
    _, _, theirs = _gear(player_equipment)
    if not mine or not theirs:
        return ""
    a, b = armour_weight(mine), armour_weight(theirs)
    same = " Neither of you is more heavily armoured than the other." if a == b else ""
    # Not "certain ... they are mistaken" (0.5.4): Nahsi then argued. Mods can
    # make light armour look heavy (Improved Fighters Guild: its light and heavy
    # sets share one model), so the traveller may have a point about the look.
    # MVXK21, Nov 11: "a simple correction that this is light armor, but it
    # definitely looks heavy doesn't it?"
    return (f"In short: you are wearing {a}, and the traveller is wearing {b}.{same} You know this "
            "for sure, but armour can look heavier or lighter than it is. If the traveller gets "
            "it wrong, correct them simply and good-naturedly; don't blame their eyes or the light. "
            "Agree that it may well look that way, and say what you like about your armour.")


def describe(sheet) -> list[str]:
    """The prompt's lines on what the NPC was doing and what they see."""
    lines = []
    doing = describe_activity(getattr(sheet, "activity", {}))
    if doing:
        lines.append(doing)
    gear = describe_equipment(getattr(sheet, "equipment", ()), _n(getattr(sheet, "activity", {}), "weapon_out"))
    if gear:
        lines.append(gear)
    seen = describe_player(getattr(sheet, "player_seen", {}))
    if seen:
        lines.append("What you can see of the traveller: " + " ".join(seen)
                     + " React to it as someone like you would, if it matters to you.")
    seen_dict = getattr(sheet, "player_seen", {})
    both = compare_armour(getattr(sheet, "equipment", ()),
                          seen_dict.get("equipment") if isinstance(seen_dict, dict) else None)
    if both:
        lines.append(both)
    return lines
