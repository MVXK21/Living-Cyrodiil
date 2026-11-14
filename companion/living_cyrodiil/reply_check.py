"""Cleans up an LLM reply and checks it for obvious lore and character breaks.

Cleaning (always applied): strips quotes, a leading "Name:", *actions*,
(stage directions), markdown and emoji, and trims to four sentences.
Checking: modern or out-of-world terms, and "as an AI"-style meta talk.
A failed reply is regenerated once by the conversation (see conversation.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_SENTENCES = 4

META_PATTERNS = [
    r"\bas an? (ai|assistant|language model)\b",
    r"\bi('m| am) (just )?(an? )?(ai|assistant|chatbot|bot)\b",
    r"\b(in|out of) character\b",
    r"\bthis (game|roleplay|simulation)\b",
]

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Narrated actions without asterisks (Nov 10 test, Shamada: "Shamada's ears
# flick toward the traveller, and her tail gives a lazy curl."). Khajiit speak
# of themselves by name, so a sentence counts as narration only if it starts
# with the NPC (name, she, he, her, his), describes body language, and
# speaks to no one: no "you", "I" or "this one".
_BODY_LANGUAGE = re.compile(
    r"\b(ears?|tail|brows?|whiskers|gaze|claws?|"
    r"flick(s|ed)?|curl(s|ed)?|twitch(es|ed)?|narrows?|shrugs?|nods?|smiles?|grins?|sighs?|glances?|purrs?|"
    r"chuckles?|leans?|tilts?|gestures?|frowns?|snorts?|sidelong|flatten(s|ed)?|"
    r"looks? (down|up|back|away)|looks? at (her|his) own)\b", re.IGNORECASE)
# "...then back at you with a flick of her tail" is still narration (Nov 11
# replay, Nahsi): "you" after a preposition doesn't make it speech.
_AT_YOU = re.compile(r"\b(at|to|toward|towards|on|over|past|with|from) (you|yours)\b", re.IGNORECASE)
_SPOKEN = re.compile(r"\b(you|your|yours|ye|i|i'm|i've|i'd|me|my|mine|we|us|our|this one)\b", re.IGNORECASE)
_PLAYER_AS_THEM = re.compile(r"\bthe (traveller|traveler|stranger)\b", re.IGNORECASE)


def is_narration(sentence: str, npc_name: str = "") -> bool:
    s = sentence.strip()
    if not _BODY_LANGUAGE.search(s) or _SPOKEN.search(_AT_YOU.sub(" ", s)):
        return False
    # "glances down at herself, then back at the traveller, tail flicking."
    # (a "Nahsi: glances..." reply once its "Name:" was stripped).
    if re.match(r"[a-z]", s) and _BODY_LANGUAGE.match(s):
        return True
    names = [npc_name, npc_name.split()[0]] if npc_name else []
    if any(re.match(rf"{re.escape(n)}('s)?\b", s, re.IGNORECASE) for n in names):
        return True
    # "She smiles at the traveller": a pronoun only counts with the player
    # spoken of as someone else ("She looks tired" can be about anyone).
    return bool(re.match(r"(she|he|her|his)\b", s, re.IGNORECASE)) and bool(_PLAYER_AS_THEM.search(s))


@dataclass
class CheckResult:
    text: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def clean_reply(text: str, npc_name: str = "") -> str:
    t = text.strip()
    # stage directions: *smiles*, (sighs), [laughs]. Up to a few sentences long:
    # models write things like "*Harald looks up from the anvil, wiping soot
    # from his brow with the back of a scarred hand.*" (lore bench, Nov 4, 2026).
    t = re.sub(r"\*[^*\n]{0,400}\*", " ", t)
    t = re.sub(r"\([^)\n]{0,300}\)", " ", t)
    t = re.sub(r"\[[^\]\n]{0,300}\]", " ", t)
    # leftover markdown and emoji
    t = t.replace("**", "").replace("__", "").replace("#", "")
    t = _EMOJI.sub("", t)
    # a leading "Name:" (full name or first name)
    if npc_name:
        names = {npc_name, npc_name.split()[0]}
        for n in names:
            t = re.sub(rf"^\s*{re.escape(n)}\s*:\s*", "", t, flags=re.IGNORECASE)
    # surrounding quotes
    t = t.strip().strip('"').strip("\u201c\u201d").strip()
    t = re.sub(r"\s+", " ", t).strip()
    # narrated actions, and at most MAX_SENTENCES sentences
    sentences = [s for s in _SENTENCE_END.split(t) if s and not is_narration(s, npc_name)]
    t = " ".join(sentences)
    if len(sentences) > MAX_SENTENCES:
        t = " ".join(sentences[:MAX_SENTENCES])
    return t


def _term_pattern(term: str) -> re.Pattern:
    return re.compile(rf"(?<![\w']){re.escape(term)}(?![\w'])", re.IGNORECASE)


def check_reply(
    raw: str,
    npc_name: str,
    banned_terms: tuple[str, ...] | list[str],
    player_line: str = "",
) -> CheckResult:
    text = clean_reply(raw, npc_name)
    result = CheckResult(text=text)

    if not text:
        result.problems.append("empty reply")
        return result

    # Curly apostrophes count as straight ones, so "n’wah" matches "n'wah".
    plain_text = text.replace("’", "'").replace("‘", "'")
    plain_player = player_line.replace("’", "'").replace("‘", "'")
    player_lower = plain_player.lower()
    for term in banned_terms:
        pat = _term_pattern(term)
        # A word the traveller used is fine to echo back in confusion, and so
        # is part of one ("a smart... phone?" after "What's a smartphone?").
        if pat.search(plain_text) and term.lower() not in player_lower:
            result.problems.append(f"out-of-world term: {term!r}")

    for meta in META_PATTERNS:
        if re.search(meta, text, re.IGNORECASE):
            result.problems.append("talks about being an AI or a character")
            break

    return result
