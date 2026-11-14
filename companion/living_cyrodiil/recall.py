"""What an NPC recalls of the player from earlier meetings (Phase 5 step 2c-d).

* `describe_ago`: "6 days ago", from in-game days (the plugin's days_passed).
* `summarize`: when a conversation ends, one small LLM call writes what was
  talked about and how the NPC felt, plus facts the player shared about
  themselves (name, home, work, plans, promises).
* `memory_note`: the prompt's section on earlier meetings, built from those
  summaries.
"""

from __future__ import annotations

import logging
import re

from .memory import Remembered

log = logging.getLogger(__name__)

MAX_SUMMARIES = 6          # earlier meetings the prompt mentions, newest last
MAX_FACTS = 10             # facts about the player the prompt lists


def describe_ago(now: float | None, then: float | None) -> str:
    """How long ago, in game time, in plain words ("" if either is unknown)."""
    if now is None or then is None:
        return ""
    days = now - then
    if days < 0:
        return ""
    if days < 0.1:                        # under about 2 hours
        return "just a little while ago"
    if int(now) == int(then):
        return "earlier today"
    whole = int(now) - int(then)          # calendar days apart
    if whole == 1:
        return "yesterday"
    if whole < 7:
        return f"{whole} days ago"
    if whole < 14:
        return "about a week ago"
    if whole < 30:
        return f"about {whole // 7} weeks ago"
    if whole < 60:
        return "about a month ago"
    if whole < 365:
        return f"about {whole // 30} months ago"
    return "more than a year ago"


SUMMARY_PROMPT = """You keep the memory of {name}, a {race} {class_name} in Cyrodiil, about a traveller \
they talked with. Below is that conversation. Write two parts, exactly in this form:

SUMMARY: one or two short sentences, as {name}'s own memory in the first person (for example \
"The traveller asked me about ... and I told them ..."): what was talked about, anything agreed or \
promised, and how I felt about the traveller.
FACTS: what the traveller said about themselves, one per line starting with "- " (their name, where \
they're from, their work, plans, promises, anything personal). Only what they actually said. Write \
"- none" if they shared nothing.

Don't invent anything that isn't in the conversation."""

# Nov 9 playtest: asked what he remembered, a guard spent the meeting
# retelling the last one, and its summary repeated the earlier summary.
ALREADY_REMEMBERED = """

What I already remember from earlier meetings (don't repeat any of it; write only what's new in this conversation, and leave out facts already known):
{earlier}

If nothing new was said (for example, we only went over what I already remembered), write exactly:
SUMMARY: nothing new
FACTS:
- none"""

NOTHING_NEW = "nothing new"


def _transcript(exchanges: list[Remembered]) -> str:
    lines = []
    for e in exchanges:
        if e.vanilla:
            lines.append(f"(In ordinary conversation, about {e.player or 'greetings'}) Me: {e.npc}")
        else:
            lines.append(f"Traveller: {e.player}")
            lines.append(f"Me: {e.npc}")
    return "\n".join(lines)


def parse_summary(text: str) -> tuple[str, list[str]]:
    """(summary, facts) from the LLM's answer. Tolerant: a reply without the
    labels becomes the summary, with no facts."""
    text = text.strip()
    m = re.search(r"SUMMARY:\s*(.*?)(?:\n\s*FACTS:|\Z)", text, re.S | re.I)
    summary = " ".join((m.group(1) if m else text).split())
    facts: list[str] = []
    f = re.search(r"FACTS:\s*(.*)\Z", text, re.S | re.I)
    if f:
        for line in f.group(1).splitlines():
            line = line.strip().lstrip("-*• ").strip()
            if line and line.lower().rstrip(".") not in ("none", "nothing", "n/a"):
                facts.append(line)
    return summary, facts


def summarize(llm, name: str, race: str, class_name: str, exchanges: list[Remembered],
              earlier: list[str] | None = None) -> tuple[str, list[str]]:
    """One small LLM call. `earlier`: summaries the NPC already has, so this
    one adds only what's new. Returns ("", []) when nothing new was said.
    Raises the LLM's own errors."""
    system = SUMMARY_PROMPT.format(name=name, race=race, class_name=class_name.lower())
    if earlier:
        system += ALREADY_REMEMBERED.format(earlier="\n".join(f"- {s}" for s in earlier))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": _transcript(exchanges)}]
    result = llm.complete(messages)
    text, facts = parse_summary(result.text)
    if text.lower().strip(" .") == NOTHING_NEW:
        return "", []
    return text, facts


def memory_note(summaries: list[dict], now: float | None) -> str:
    """The prompt's section on earlier meetings ("" if there are none)."""
    if not summaries:
        return ""
    lines = ["What you remember of this traveller from earlier meetings (oldest first):"]
    for s in summaries[-MAX_SUMMARIES:]:
        ago = describe_ago(now, s.get("day"))
        lines.append(f"- {ago[0].upper() + ago[1:]}: {s['text']}" if ago else f"- {s['text']}")
    facts: list[str] = []
    seen: set[str] = set()
    for s in reversed(summaries):              # newest first, so newer facts win
        for fact in s.get("facts", []):
            key = fact.lower().strip(" .")
            if key and key not in seen:
                seen.add(key)
                facts.append(fact)
    if facts:
        facts = list(reversed(facts[:MAX_FACTS]))
        lines.append("What the traveller has told you about themselves:")
        lines.extend(f"- {fact}" for fact in facts)
    return "\n".join(lines)
