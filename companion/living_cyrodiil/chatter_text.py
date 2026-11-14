"""Who an NPC was just talking to (plugin 0.18.0; MVXK21, Nov 10: asked what
they'd been talking about, NPCs didn't know they'd been talking at all).

The plugin notices NPCs near the player speaking to each other and sends the
NPC's recent lines ("recent_chatter": who with, how long ago). What was said
isn't known: 0.18.0 and 0.18.1 tried to identify the lines from the voice
files the game looked up, but the game looks up bursts of candidate lines at
once, so the words were often wrong (Nov 10 test: Shamada quoted a line she
probably never said and made up the rest). So the NPC knows the conversation
happened and with whom, and keeps its content private (MVXK21's fallback).
"""

from __future__ import annotations


def describe(sheet) -> str:
    """The prompt's note on who the NPC was just talking to ("" if nobody).
    Only their latest chat partner counts."""
    lines = getattr(sheet, "recent_chatter", ())
    if not lines:
        return ""
    partner = lines[-1].other_name
    when = "a moment ago" if lines[-1].seconds_ago < 60 else "a few minutes ago"
    return (f"Just before the traveller came up ({when}), you were talking with {partner}. You know this and "
            "never deny it. What you two said is your own business: if the traveller asks, don't repeat or make up "
            "any of it; say it was a private conversation, nothing that concerns them.")
