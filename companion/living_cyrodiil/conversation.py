"""One conversation with one NPC: history, prompt, LLM call, check and retry.

Kept free of game and network code so it can be tested on its own. The game
connection (Phase 2 step 3) and the type-to-chat tool both drive this class.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .character import CharacterSheet
from .llm import LLMClient, LLMError
from .prompt import Turn, build_messages
from .reply_check import MAX_SENTENCES, check_reply
from .reply_stream import SentenceSplitter, first_part_ready
from .subtitles import split_into_chunks
from .setting_profile import SettingProfile

log = logging.getLogger(__name__)

# Keep this many recent exchanges word for word. Older memory is Phase 5.
MAX_HISTORY = 8


@dataclass
class ReplyInfo:
    text: str
    seconds: float              # total, including any retry
    attempts: int
    used_fallback: bool = False
    problems: list[str] = field(default_factory=list)   # from the first attempt
    parts: list[str] = field(default_factory=list)      # streamed replies: the subtitle parts, part 1 first
    first_part_seconds: float | None = None             # streamed replies: when part 1 was ready
    cut_short: str = ""                                 # streamed replies: why it ended early, if it did


class Conversation:
    def __init__(self, sheet: CharacterSheet, profile: SettingProfile, llm: LLMClient, world=None):
        self.sheet = sheet
        self.profile = profile
        self.llm = llm
        self.world = world              # the player's WorldKnowledge, or None
        self.history: list[Turn] = []
        # Phase 5 memory: told about each new exchange, (player, npc, vanilla).
        self.on_remember: Callable[[str, str, bool], None] | None = None
        # Phase 5 memory: what the NPC recalls of earlier meetings, for the prompt.
        self.memory_note = ""
        # When the plugin last let the NPC go (in-game days, and real time),
        # or None while they're talking. A while later, talking again is a
        # new meeting.
        self.over_day: float | None = None
        self.over_at: float | None = None

    def _remember(self, player: str, npc: str, vanilla: bool) -> None:
        if self.on_remember:
            try:
                self.on_remember(player, npc, vanilla)
            except Exception:
                log.exception("[%s] saving to memory failed", self.sheet.name)

    def reset(self) -> None:
        self.history.clear()

    def add_vanilla(self, topic: str, text: str) -> None:
        """Something the NPC said in the game's own dialogue window, in the order
        it happened. Lines on the same topic (a long answer) join one exchange."""
        topic, text = topic.strip(), " ".join(text.split())
        if not text:
            return
        last = self.history[-1] if self.history else None
        if last and last.vanilla and last.player == topic:
            if text in last.npc:
                return
            self.history[-1] = Turn(player=topic, npc=f"{last.npc} {text}", vanilla=True)
        else:
            self.history.append(Turn(player=topic, npc=text, vanilla=True))
        del self.history[:-MAX_HISTORY]
        log.info("[%s] vanilla%s: %s", self.sheet.name, f" ({topic})" if topic else "", text)
        self._remember(topic, text, True)

    def reply(self, player_line: str) -> ReplyInfo:
        player_line = player_line.strip()
        start = time.perf_counter()
        log.info("[%s] player: %s", self.sheet.name, player_line)

        first_problems: list[str] = []
        correction = None
        for attempt in (1, 2):
            messages = build_messages(self.sheet, self.profile, self.history, player_line, correction,
                                      world=self.world, memory_note=self.memory_note)
            log.debug("Prompt (attempt %d):\n%s", attempt, _format_messages(messages))
            result = self.llm.complete(messages)        # may raise LLMError
            check = check_reply(result.text, self.sheet.name, self.profile.banned_for(self.sheet.race), player_line)
            if check.ok:
                return self._finish(player_line, check.text, start, attempt, False, first_problems)

            log.warning("[%s] reply broke character (%s): %r",
                        self.sheet.name, "; ".join(check.problems), result.text)
            if attempt == 1:
                first_problems = check.problems
                correction = (
                    "Your previous answer broke character ("
                    + "; ".join(check.problems)
                    + "). Answer again, fully in character, following every rule."
                )

        fallback = random.choice(self.profile.fallback_lines)
        return self._finish(player_line, fallback, start, 2, True, first_problems)

    # -- streamed replies (Phase 3 step 4b) -------------------------------------

    def reply_streaming(self, player_line: str, on_first_part: Callable[[str], None]) -> ReplyInfo:
        """Like reply(), but the LLM's reply is read as it's written. Each
        sentence is cleaned and checked as soon as it's complete; part 1 (the
        first sentence, or two short ones) goes to `on_first_part` straight
        away, while the rest is still being written. The returned ReplyInfo
        has every part in `parts`, part 1 first and unchanged.

        A bad first sentence: the reply is asked for again, as reply() does.
        A bad later sentence: the reply ends at the last good one (part 1 has
        already been said). Runs in a worker thread; `on_first_part` is called
        from it, at most once."""
        player_line = player_line.strip()
        start = time.perf_counter()
        log.info("[%s] player: %s", self.sheet.name, player_line)
        banned = self.profile.banned_for(self.sheet.race)
        first_problems: list[str] = []
        correction = None
        part1: str | None = None
        part1_at: float | None = None

        for attempt in (1, 2):
            messages = build_messages(self.sheet, self.profile, self.history, player_line, correction,
                                      world=self.world, memory_note=self.memory_note)
            log.debug("Prompt (attempt %d, streamed):\n%s", attempt, _format_messages(messages))
            splitter = SentenceSplitter()
            good: list[str] = []
            state = {"bad": [], "cut": ""}

            def take(sentence: str) -> bool:
                """One finished sentence. Returns False to stop the stream."""
                nonlocal part1, part1_at
                check = check_reply(sentence, self.sheet.name, banned, player_line)
                cleaned = check.text
                if not cleaned:
                    log.info("[%s] dropped a sentence that was only a stage direction: %r", self.sheet.name, sentence)
                    return True
                if not check.ok:
                    state["bad"] = check.problems
                    log.warning("[%s] sentence broke character (%s): %r",
                                self.sheet.name, "; ".join(check.problems), sentence)
                    if good:
                        state["cut"] = "a later sentence broke character"
                    return False
                good.append(cleaned)
                if part1 is None:
                    ready = first_part_ready(good, finished=False)
                    if ready is not None:
                        part1, part1_at = ready, time.perf_counter() - start
                        log.info("[%s] part 1 ready after %.2fs: %s", self.sheet.name, part1_at, part1)
                        on_first_part(part1)
                if len(good) >= MAX_SENTENCES:
                    state["cut"] = state["cut"] or f"{MAX_SENTENCES} sentences"
                    return False
                return True

            def on_text(piece: str) -> bool:
                for sentence in splitter.feed(piece):
                    if not take(sentence):
                        return False
                return True

            result = self.llm.complete_streaming(messages, on_text)    # may raise LLMError
            if not result.stopped:
                rest = splitter.flush()
                if rest:
                    if result.finish_reason == "length" and rest[-1] not in ".!?…\"'":
                        log.info("[%s] dropped the cut-off end of the reply: %r", self.sheet.name, rest)
                    else:
                        take(rest)

            if good:                           # at least one good sentence: that's the reply
                if part1 is None:              # one short sentence and nothing more
                    part1, part1_at = first_part_ready(good, finished=True), time.perf_counter() - start
                    log.info("[%s] part 1 ready after %.2fs: %s", self.sheet.name, part1_at, part1)
                    on_first_part(part1)
                return self._finish_streamed(player_line, good, part1, part1_at, start, attempt,
                                             first_problems, state["cut"])
            problems = state["bad"] or ["empty reply"]
            log.warning("[%s] reply unusable before its first part (%s): %r",
                        self.sheet.name, "; ".join(problems), result.text)
            if attempt == 1:
                first_problems = problems
                correction = (
                    "Your previous answer broke character ("
                    + "; ".join(problems)
                    + "). Answer again, fully in character, following every rule."
                )

        fallback = random.choice(self.profile.fallback_lines)
        on_first_part(fallback)
        info = self._finish(player_line, fallback, start, 2, True, first_problems)
        info.parts = [fallback]
        info.first_part_seconds = info.seconds
        return info

    def _finish_streamed(self, player_line, good, part1, part1_at, start, attempts, problems, cut) -> ReplyInfo:
        text = " ".join(good)
        rest = text[len(part1):].strip() if text.startswith(part1) else ""
        parts = [part1] + (split_into_chunks(rest, max_chunks=3) if rest else [])
        info = self._finish(player_line, text, start, attempts, False, problems)
        info.parts = parts
        info.first_part_seconds = part1_at
        info.cut_short = cut if cut and cut != f"{MAX_SENTENCES} sentences" else ""
        if info.cut_short:
            log.info("[%s] reply ended early: %s", self.sheet.name, info.cut_short)
        return info

    def _finish(self, player_line, text, start, attempts, fallback, problems) -> ReplyInfo:
        self.history.append(Turn(player=player_line, npc=text))
        del self.history[:-MAX_HISTORY]
        self._remember(player_line, text, False)
        info = ReplyInfo(text=text, seconds=time.perf_counter() - start,
                         attempts=attempts, used_fallback=fallback, problems=problems)
        log.info("[%s] npc (%.2fs, attempts %d%s): %s", self.sheet.name, info.seconds,
                 attempts, ", fallback" if fallback else "", text)
        return info


def _format_messages(messages: list[dict]) -> str:
    return "\n".join(f"--- {m['role']} ---\n{m['content']}" for m in messages)


__all__ = ["Conversation", "ReplyInfo", "LLMError"]
