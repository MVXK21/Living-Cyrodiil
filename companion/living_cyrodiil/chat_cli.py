"""Type-to-chat test tool: talk to a sample NPC in the terminal, no game needed.

Run with companion\\chat.ps1. Commands while chatting:
  /npc     pick a different NPC        /reset   forget this conversation
  /prompt  show the instructions the NPC gets      /quit    leave
"""

from __future__ import annotations

import logging
import sys

from .character import CharacterSheet, load_sample_npcs
from .conversation import Conversation, LLMError
from .llm import LLMClient
from .logs import setup_logging
from .paths import LOG_DIR, SETTINGS_FILE
from .prompt import build_system_prompt
from .settings import SettingsError, load_settings
from .setting_profile import load_profile

log = logging.getLogger("chat")


def pick_npc(npcs: list[CharacterSheet]) -> CharacterSheet:
    print("\nWho do you want to talk to?")
    for i, n in enumerate(npcs, 1):
        print(f"  {i}. {n.name} ({n.sex} {n.race} {n.class_name.lower()}, {n.location})")
    while True:
        choice = input("Number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(npcs):
            return npcs[int(choice) - 1]
        print(f"Type a number from 1 to {len(npcs)}.")


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as e:
        print(f"\n{e}\n")
        return 1

    setup_logging(settings.log_level)
    log.info("Chat tool started. Model: %s", settings.llm.model)

    profile = load_profile(settings.setting_profile)
    npcs = load_sample_npcs()
    llm = LLMClient(settings.llm)

    print("Living Cyrodiil: type-to-chat test")
    print(f"Model: {settings.llm.model}   (change it in {SETTINGS_FILE.name})")
    print("Commands: /npc  /reset  /prompt  /quit")

    convo = Conversation(pick_npc(npcs), profile, llm)
    print(f"\nYou approach {convo.sheet.name}.\n")

    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/npc":
            convo = Conversation(pick_npc(npcs), profile, llm)
            print(f"\nYou approach {convo.sheet.name}.\n")
            continue
        if line == "/reset":
            convo.reset()
            print("(Conversation forgotten.)\n")
            continue
        if line == "/prompt":
            print("\n" + build_system_prompt(convo.sheet, profile) + "\n")
            continue

        try:
            info = convo.reply(line)
        except LLMError as e:
            print(f"(Problem: {e})\n")
            log.error("LLM error: %s", e)
            continue

        note = f"{info.seconds:.1f}s"
        if info.attempts > 1:
            note += ", retried: " + "; ".join(info.problems)
        if info.used_fallback:
            note += ", used a fallback line"
        print(f"{convo.sheet.name}: {info.text}")
        print(f"   ({note})\n")

    print(f"Goodbye. The full log is in {LOG_DIR / 'companion.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
