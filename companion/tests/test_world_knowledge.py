"""Step 7b: the player's world knowledge entries."""

from __future__ import annotations

import os
import time

import pytest

from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.prompt import build_messages
from living_cyrodiil.setting_profile import load_profile
from living_cyrodiil.world_knowledge import TEMPLATE, WorldKnowledge, parse

PEACE = ("The Empire is at peace, the Emperor is alive, the Mythic Dawn is unknown, "
         "no Oblivion gates have opened, Kvatch still stands.")


@pytest.fixture(scope="module")
def profile():
    return load_profile("vanilla")


def marcus():
    return CharacterSheet(name="Marcus", race="Imperial", sex="male", class_name="Publican",
                          location="a tavern in Leyawiin")


def write(path, text):
    path.write_text(text, encoding="utf-8")
    # make sure the change is seen even on filesystems with coarse timestamps
    t = time.time() + 2
    os.utime(path, (t, t))


def test_parse_on_off_replace_and_continuation():
    entries = parse(
        "# a note\n"
        "[on, replace] 1: The Emperor lives.\n"
        "[OFF] 2: A plague in Bravil.\n"
        "[on] 3: Long entry that\n"
        "   carries on here.\n"
        "nonsense line\n"
    )
    assert [(e.number, e.enabled, e.replace) for e in entries] == [(1, True, True), (2, False, False), (3, True, False)]
    assert entries[2].text == "Long entry that carries on here."


def test_template_parses_with_both_examples_off():
    entries = parse(TEMPLATE)
    assert len(entries) == 2 and not any(e.enabled for e in entries)
    assert entries[0].replace and not entries[1].replace


def test_file_is_created_and_reloaded_live(tmp_path):
    path = tmp_path / "world_knowledge.txt"
    wk = WorldKnowledge(path)
    assert path.exists() and wk.active() == []
    write(path, f"[on, replace] 1: {PEACE}\n")
    assert [e.number for e in wk.active()] == [1]
    write(path, f"[off, replace] 1: {PEACE}\n")
    assert wk.active() == []


def test_enabled_entry_leads_the_prompt_and_replaces_world_facts(profile, tmp_path):
    path = tmp_path / "wk.txt"
    write(path, f"[on, replace] 1: {PEACE}\n")
    system = build_messages(marcus(), profile, [], "Any news of the Emperor?", world=WorldKnowledge(path))[0]["content"]
    assert "The truth of this world" in system and PEACE in system
    assert "assassinated" not in system                          # the default world fact is gone
    assert system.index("The truth of this world") < system.index("Rules:")


def test_add_entry_keeps_world_facts(profile, tmp_path):
    path = tmp_path / "wk.txt"
    write(path, "[on] 1: A plague has struck Bravil.\n")
    system = build_messages(marcus(), profile, [], "Any news?", world=WorldKnowledge(path))[0]["content"]
    assert "A plague has struck Bravil." in system and "assassinated" in system


def test_covered_lore_is_left_out(profile, tmp_path):
    path = tmp_path / "wk.txt"
    question = "Tell me about Emperor Uriel Septim and his sons."
    off = build_messages(marcus(), profile, [], question, world=None)[0]["content"]
    assert "Geldall" in off                                       # lore entry 3e-uriel-vii is used
    sons = "[on] 1: Uriel Septim VII abdicated, and his sons now rule as a council of three."
    write(path, sons + "\n")
    on = build_messages(marcus(), profile, [], question, world=WorldKnowledge(path))[0]["content"]
    assert "Geldall" not in on                                    # same subject: the entry wins
    write(path, sons.replace("[on]", "[off]") + "\n")
    back = build_messages(marcus(), profile, [], question, world=WorldKnowledge(path))[0]["content"]
    assert "Geldall" in back and "The truth of this world" not in back


def test_passing_mention_keeps_background_lore(profile, tmp_path):
    # "the Emperor is alive" mentions the Emperor once; who he is must stay known.
    path = tmp_path / "wk.txt"
    write(path, f"[on, replace] 1: {PEACE}\n")
    system = build_messages(marcus(), profile, [], "How is the Emperor these days?",
                            world=WorldKnowledge(path))[0]["content"]
    assert "Uriel Septim VII" in system


def test_disabled_entries_are_not_sent(profile, tmp_path):
    path = tmp_path / "wk.txt"
    write(path, "[off] 1: The moons have turned green.\n")
    system = build_messages(marcus(), profile, [], "Hello.", world=WorldKnowledge(path))[0]["content"]
    assert "green" not in system


def test_replace_entry_drops_crisis_lore_and_bio_parts(profile, tmp_path):
    # MVXK21, Nov 12: with entry 1 (the Emperor alive) on, an Imperial City guard
    # spoke of "the Emperor's murder" (lore entries assumed the crisis).
    guard = CharacterSheet(name="Imperial Guardian", race="Imperial", sex="male", class_name="Guard",
                           location="Talos Plaza District, in Imperial City")
    ocato = CharacterSheet(name="High Chancellor Ocato", race="High Elf", sex="male", class_name="Battlemage",
                           location="Imperial Palace, in Imperial City", plugin="Oblivion.esm")
    question = "Is there much crime? What of the Emperor and the Blades, and the palace?"
    path = tmp_path / "wk.txt"
    write(path, f"[on, replace] 1: {PEACE}\n")
    for who in (guard, ocato):
        on = build_messages(who, profile, [], question, world=WorldKnowledge(path))[0]["content"].lower()
        assert "murder" not in on and "throne stands empty" not in on and "throne is empty" not in on
    # The default story keeps the crisis.
    write(path, f"[off] 1: {PEACE}\n")
    off = build_messages(guard, profile, [], question, world=WorldKnowledge(path))[0]["content"].lower()
    assert "emperor's murder" in off
    assert "throne stands empty" in build_messages(ocato, profile, [], question)[0]["content"].lower()
