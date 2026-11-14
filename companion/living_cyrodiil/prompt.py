"""Builds the messages sent to the LLM for one conversation turn.

Phase 2 uses DESIGN.md layers 1 (character sheet) and 6 (ground rules), plus
location, time of day, the setting's common-knowledge world facts, an expertise
note from the NPC's skills (what they know deeply and what's beyond them) and a
pilot of the lore library (layer 4). Memory, bios and live quest state arrive
in Phase 5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import awareness, books, chatter_text, expertise, world_events, world_state
from .character import CharacterSheet
from .setting_profile import SettingProfile
from .world_knowledge import WorldKnowledge


@dataclass(frozen=True)
class Turn:
    player: str
    npc: str
    # True for an exchange in the game's own dialogue window (step 8): `player`
    # is the topic clicked ("" for the greeting), `npc` what the NPC said.
    vanilla: bool = False
    # True for an exchange remembered from an earlier visit (Phase 5 memory),
    # not part of the conversation happening now; `when` says how long ago
    # ("6 days ago"), "" if unknown.
    earlier: bool = False
    when: str = ""


EARLIER_VISIT_NOTE = (
    "You have met this traveller before. Exchanges marked (earlier visit) happened on a previous meeting, "
    "not just now: this is a new meeting, so don't carry on from them mid-thought. Remember what you learned "
    "and bring it up naturally when it fits.")


def _player_message(turn: Turn) -> str:
    text = _player_words(turn)
    if not turn.earlier:
        return text
    return f"(earlier visit, {turn.when}) {text}" if turn.when else f"(earlier visit) {text}"


def _player_words(turn: Turn) -> str:
    if not turn.vanilla:
        return turn.player
    if turn.player:
        return f"(In ordinary conversation, the traveller asks: {clean_topic(turn.player)})"
    return "(The traveller comes up to you in ordinary conversation.)"


def describe_disposition(value: int) -> str:
    if value < 20:
        return "You dislike this person and want them gone."
    if value < 40:
        return "You are cold and wary toward this person."
    if value < 60:
        return "You are neutral toward this person: civil, but not warm."
    if value < 80:
        return "You are friendly toward this person."
    return "You like this person a great deal and are warm toward them."


def describe_time(hour: float) -> str:
    h = hour % 24
    if h < 5:
        return "the dead of night"
    if h < 8:
        return "early morning"
    if h < 12:
        return "morning"
    if h < 14:
        return "midday"
    if h < 18:
        return "afternoon"
    if h < 21:
        return "evening"
    return "night"


def clean_topic(topic: str) -> str:
    """'1. Tell the guards.' -> 'Tell the guards' (NorthernUI numbers topics)."""
    return re.sub(r"^\s*\d+\s*[.)]\s*", "", topic).strip().rstrip(".")


def describe_where(sheet: CharacterSheet) -> str:
    """Indoors, on a town's streets, or out in the country. Nov 5 playtest:
    "outdoors" in Leyawiin read as the county, and Betto Plotius, on Main
    Street, talked as if he were out on the road."""
    if sheet.interior:
        # Nov 9 playtest: Agata, inside the Leyawiin Mages Guild, told the
        # player to come to the guild hall later; "indoors" alone didn't say
        # the hall was where they both stood.
        if sheet.location and sheet.location != "Cyrodiil":
            return (f"indoors, inside {sheet.location} itself, and the traveller is here with you: "
                    "never tell them to come or go to this place, they are already in it")
        return "indoors"
    if sheet.in_town:
        return f"outdoors on the streets of {sheet.location} itself, inside its walls, not out on the road"
    if sheet.location and sheet.location != "Cyrodiil":
        return f"outdoors at {sheet.location}"
    if sheet.surroundings:
        return f"outdoors in the open country, {sheet.surroundings}"
    return "outdoors in the open country, away from any town"


def build_system_prompt(sheet: CharacterSheet, profile: SettingProfile, talk: str = "",
                        world: WorldKnowledge | None = None, vanilla: list[Turn] | None = None) -> str:
    """`talk` is what's being talked about (the player's line and the NPC's last
    reply); it picks which lore entries the NPC gets. `world` is the player's
    world knowledge file: its enabled entries are the truth of this world.
    `vanilla`: what the NPC said in the game's own dialogue window this visit."""
    lines: list[str] = []
    truths = world.active() if world else []
    # The player's own version of Cyrodiil: an entry that replaces the world
    # facts leaves out the default story's crisis everywhere (quest news, world
    # facts, crisis lore, the crisis part of bios).
    replace = bool(truths) and any(e.replace for e in truths)

    lines.append(
        f"You are {sheet.name}, a {sheet.sex} {sheet.race} {sheet.class_name.lower()} "
        f"in {sheet.location}, in the province of Cyrodiil. The year is 3E 433."
    )
    bio = profile.bios.for_npc(sheet.name, sheet.plugin, own_world=replace) if profile.bios else ""
    if bio:
        lines.append(f"Who you are: {bio}")
    if sheet.factions:
        parts = [f"{f.name} ({f.rank_title})" if f.rank_title else f.name for f in sheet.factions]
        lines.append("You belong to: " + "; ".join(parts) + ".")
    if sheet.notes:
        lines.append(sheet.notes)

    if truths:
        lines.append("The truth of this world. These facts are certain; you know them as anyone "
                     "living here would, and anything else in these instructions or in your own "
                     "memory that disagrees with them is wrong:")
        lines.extend(f"- {e.text}" for e in truths)

    lines.append(f"It is {describe_time(sheet.hour)}, and you are {describe_where(sheet)}.")
    # Phase 5 step 3a: the date, the weather, and what this NPC knows of the
    # traveller's standing (guild ranks, bounty, reputation).
    now = " ".join(x for x in (world_state.describe_date(sheet, profile), world_state.describe_weather(sheet)) if x)
    if now:
        lines.append(now)
    lines.extend(world_state.describe_standing(sheet, profile))
    mood = describe_disposition(sheet.disposition) if sheet.disposition is not None else ""
    lines.append(
        "A traveller has come up to talk to you. You do not know their name unless "
        "they tell you. " + mood
    )
    chatter = chatter_text.describe(sheet)
    if chatter:
        lines.append(chatter)
    lines.extend(awareness.describe(sheet))      # step 4: what they're doing, what they see

    note = profile.race_note(sheet.race)
    if note:
        lines.append(f"How you speak: {note}")

    levels = expertise.levels(sheet, profile.expertise)
    knows = expertise.expertise_note(sheet, profile.expertise)
    if knows:
        lines.append(f"What you know: {knows}")

    # Step 3b: what's happened in the player's quests that this NPC has heard
    # of. A world knowledge entry that replaces the world facts means the
    # player has set up their own version of Cyrodiil, so quest news stays out.
    events = world_events.tracker.known_by(sheet) if world_events.tracker and not replace else []
    hidden = [e.hides_fact.lower() for e in events if e.hides_fact]
    facts = [f for f in profile.world_facts if not any(h in f.lower() for h in hidden)]
    if facts and not replace:
        lines.append("What everyone in Cyrodiil knows right now:")
        lines.extend(f"- {fact}" for fact in facts)
    news = [e.fact for e in events if not e.about_player]
    if news:
        lines.append("News and talk you've heard (true, and recent):")
        lines.extend(f"- {n}" for n in news)
    lines.extend(e.fact for e in events if e.about_player)

    covered = (lambda e: world.covers(e.keywords, truths)) if truths else None
    entries = profile.lore.select(sheet, levels, talk, exclude=covered, own_world=replace) if profile.lore else []
    if entries:
        lines.append("Things you know that may help. Use them only if they fit what's being "
                     "talked about, put them in your own words, and don't recite them:")
        lines.extend(f"- {e.text}" for e in entries)
    # The game's own books (step 5c): one the player names, if this NPC would know it.
    book = books.library.note(levels, talk) if books.library else ""
    if book:
        lines.append(book)

    if vanilla:
        # MVXK21, Nov 5 playtest: with these lines only in the conversation
        # history, Numeen disowned her own news ("I only know what I was
        # told", "who told you about Blankenmarch?") in 8 of 8 replays. So
        # they're stated here, after the general notes, as her own words and
        # intent, which the "never invent places" rule doesn't cover.
        lines.append("The conversation you are in the middle of. A moment ago you said this to the "
                     "traveller, in order:")
        for t in vanilla:
            topic = clean_topic(t.player)
            lines.append(f"- {f'(asked about {topic}) ' if topic else ''}{t.npc}")
        lines.append("You meant every word. What you spoke of is real and you know about it firsthand, "
                     "even places and people not mentioned anywhere else here, and it matters more than "
                     "the general notes above. Carry on in the same spirit: if you raised a worry, you are "
                     "still worried; if you asked the traveller to do something, you still want it done "
                     "and may press them. Never treat your own words as rumour or hearsay, never say you "
                     "know little about them, and never take them back. If the traveller asks about it or "
                     "says 'tell me more', answer as the one who said it, following on from what you said "
                     "most recently. Those lines also show how you really speak.")

    if profile.ground_rules:
        lines.append("Rules:")
        lines.extend(f"- {rule}" for rule in profile.ground_rules)
    if truths:
        lines.append("- The truth of this world, given above, always wins: never contradict it.")

    return "\n".join(lines)


def build_messages(
    sheet: CharacterSheet,
    profile: SettingProfile,
    history: list[Turn],
    player_line: str,
    correction: str | None = None,
    world: WorldKnowledge | None = None,
    memory_note: str = "",
) -> list[dict]:
    """OpenAI-style chat messages. `memory_note`: what the NPC recalls of
    earlier meetings (recall.memory_note). `correction` is added when retrying a reply
    that broke character, telling the model what went wrong."""
    # What's being talked about: this line, plus the last exchange (its topic
    # and reply), so "tell me more" finds lore on what was just said.
    talk = player_line
    if history:
        talk += " " + history[-1].player + " " + history[-1].npc
    system = build_system_prompt(sheet, profile, talk, world, [t for t in history if t.vanilla])
    if any(t.earlier for t in history) or memory_note:
        system += "\n" + EARLIER_VISIT_NOTE
    if memory_note:
        system += "\n" + memory_note
    if correction:
        # Kept inside the one system message: some providers merge or drop
        # system messages that appear mid-conversation.
        system += "\n" + correction
    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in history:
        messages.append({"role": "user", "content": _player_message(turn)})
        messages.append({"role": "assistant", "content": turn.npc})
    messages.append({"role": "user", "content": player_line})
    return messages
