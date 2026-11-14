"""Lore test bench (Phase 2 step 7): ask every sample NPC a fixed set of
questions through the full prompt pipeline and save every answer to a report
for MVXK21 to read. No game needed.

Run with companion\\bench.ps1. Options:
  --model NAME      use another model for this run (to compare models)
  --npc TEXT        only NPCs whose name contains TEXT (repeatable)
  --group TEXT      only question groups whose name contains TEXT (repeatable)
  --workers N       questions asked at once (default 4)
  --world FILE      use a world knowledge file for this run

Each question is asked in a fresh conversation, so answers don't influence
each other. The report (companion\\bench_results\\, not in git) flags replies
the checker had to retry or replace, and replies over four sentences.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import re
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .character import CharacterSheet, load_sample_npcs
from .conversation import Conversation, LLMError
from .llm import LLMClient
from .logs import setup_logging
from .paths import DATA_DIR, USER_DIR
from .settings import SettingsError, load_settings
from .setting_profile import load_profile
from .world_knowledge import WorldKnowledge

log = logging.getLogger("bench")

RESULTS_DIR = USER_DIR / "bench_results"
QUESTIONS_FILE = DATA_DIR / "bench_questions.toml"
MAX_SENTENCES = 4
_SENTENCE = re.compile(r"[^.!?]+[.!?]+")


@dataclass
class Answer:
    npc: str
    group: str
    question: str
    reply: str = ""
    seconds: float = 0.0
    attempts: int = 0
    used_fallback: bool = False
    problems: list[str] = field(default_factory=list)   # from the first attempt
    error: str = ""

    @property
    def sentences(self) -> int:
        return len(_SENTENCE.findall(self.reply)) or (1 if self.reply else 0)

    @property
    def flags(self) -> list[str]:
        out = []
        if self.error:
            out.append(f"error: {self.error}")
        if self.used_fallback:
            out.append("used a fallback line")
        elif self.attempts > 1:
            out.append("retried: " + "; ".join(self.problems))
        if self.sentences > MAX_SENTENCES:
            out.append(f"{self.sentences} sentences (limit {MAX_SENTENCES})")
        return out


def load_questions(path: Path = QUESTIONS_FILE) -> list[tuple[str, list[str]]]:
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    return [(g["name"], list(g["questions"])) for g in data.get("group", [])]


def ask(sheet: CharacterSheet, group: str, question: str, profile, llm, world=None) -> Answer:
    ans = Answer(npc=sheet.name, group=group, question=question)
    try:
        info = Conversation(sheet, profile, llm, world).reply(question)
    except LLMError as e:
        ans.error = str(e)
        return ans
    ans.reply, ans.seconds, ans.attempts = info.text, info.seconds, info.attempts
    ans.used_fallback, ans.problems = info.used_fallback, list(info.problems)
    return ans


def describe(sheet: CharacterSheet) -> str:
    parts = [f"{sheet.sex} {sheet.race} {sheet.class_name.lower()}", sheet.location]
    if sheet.factions:
        parts.append(", ".join(f"{f.name} ({f.rank_title})" if f.rank_title else f.name for f in sheet.factions))
    return "; ".join(parts)


def write_report(path: Path, model: str, profile_name: str, npcs: list[CharacterSheet],
                 answers: list[Answer], seconds: float) -> None:
    flagged = [a for a in answers if a.flags]
    lines = [
        f"# Lore test bench, {datetime.now():%b %d, %Y %H:%M}",
        "",
        f"Model **{model}**, setting profile **{profile_name}**. "
        f"{len(npcs)} NPCs, {len(answers)} answers in {seconds:.0f} s. "
        f"Each question was asked in a fresh conversation.",
        "",
        f"**Flagged: {len(flagged)}** (retried, fallback line, error, or over {MAX_SENTENCES} sentences).",
        "",
    ]
    if flagged:
        lines += ["| NPC | Question | Flag |", "| --- | --- | --- |"]
        lines += [f"| {a.npc} | {a.question} | {'; '.join(a.flags)} |" for a in flagged]
        lines.append("")

    by_npc: dict[str, list[Answer]] = {}
    for a in answers:
        by_npc.setdefault(a.npc, []).append(a)
    for sheet in npcs:
        lines += [f"## {sheet.name}", "", f"*{describe(sheet)}*", ""]
        group = None
        for a in by_npc.get(sheet.name, []):
            if a.group != group:
                group = a.group
                lines += [f"### {group}", ""]
            lines.append(f"**You:** {a.question}  ")
            lines.append(f"**{sheet.name}:** {a.reply or '(no reply)'}  ")
            note = f"{a.seconds:.1f} s"
            if a.flags:
                note += " · ⚠ " + "; ".join(a.flags)
            lines += [f"<sub>{note}</sub>", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Living Cyrodiil lore test bench")
    ap.add_argument("--model")
    ap.add_argument("--npc", action="append", default=[])
    ap.add_argument("--group", action="append", default=[])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--world", help="a world knowledge file to use for this run")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except SettingsError as e:
        print(f"\n{e}\n")
        return 1
    setup_logging(settings.log_level)
    llm_settings = settings.llm
    if args.model:
        llm_settings = dataclasses.replace(llm_settings, model=args.model)
    llm = LLMClient(llm_settings)
    profile = load_profile(settings.setting_profile)
    world = WorldKnowledge(Path(args.world), create=False) if args.world else None
    if world:
        print("World knowledge entries on: " + (", ".join(str(e.number) for e in world.active()) or "none"))

    npcs = [n for n in load_sample_npcs()
            if not args.npc or any(t.lower() in n.name.lower() for t in args.npc)]
    groups = [(g, qs) for g, qs in load_questions()
              if not args.group or any(t.lower() in g.lower() for t in args.group)]
    jobs = [(n, g, q) for n in npcs for g, qs in groups for q in qs]
    if not jobs:
        print("Nothing to ask: no NPCs or questions match.")
        return 1

    print(f"Lore bench: {len(npcs)} NPCs x {sum(len(q) for _, q in groups)} questions = {len(jobs)} answers, "
          f"model {llm_settings.model}")
    log.info("Bench run: %d answers, model %s", len(jobs), llm_settings.model)
    start = time.perf_counter()
    answers: list[Answer] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(ask, n, g, q, profile, llm, world) for n, g, q in jobs]
        for i, f in enumerate(futures, 1):
            answers.append(f.result())
            print(f"\r  {i}/{len(jobs)} answered", end="", flush=True)
    seconds = time.perf_counter() - start
    print()

    RESULTS_DIR.mkdir(exist_ok=True)
    safe_model = re.sub(r"[^\w.-]+", "_", llm_settings.model)
    report = RESULTS_DIR / f"bench_{datetime.now():%Y%m%d_%H%M}_{safe_model}.md"
    write_report(report, llm_settings.model, profile.name, npcs, answers, seconds)
    flagged = sum(1 for a in answers if a.flags)
    print(f"Done in {seconds:.0f} s. {flagged} flagged. Report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
