"""Step 7, lore depth: expertise levels, the expertise note and lore selection."""

from __future__ import annotations

import pytest

from living_cyrodiil import expertise
from living_cyrodiil.character import CharacterSheet, FactionMembership
from living_cyrodiil.lore import LoreEntry, LoreLibrary
from living_cyrodiil.prompt import build_messages
from living_cyrodiil.setting_profile import load_profile


@pytest.fixture(scope="module")
def profile():
    return load_profile("vanilla")


def npc(name="Test", cls="Commoner", location="Leyawiin", skills=None, factions=()):
    return CharacterSheet(name=name, race="Imperial", sex="male", class_name=cls, location=location,
                          skills=skills or {}, factions=tuple(FactionMembership(f) for f in factions))


MAGE = dict(cls="Mage", skills={"Mysticism": 80, "Alteration": 72}, factions=("Mages Guild",))
SMITH = dict(cls="Smith", skills={"Armorer": 78, "Blunt": 50})


def test_levels_from_skills_class_and_faction(profile):
    rules = profile.expertise
    mage = expertise.levels(npc(**MAGE), rules)
    smith = expertise.levels(npc(**SMITH), rules)
    assert mage["magic"] == expertise.DEEP and mage["smithing"] == expertise.NONE
    assert smith["smithing"] == expertise.DEEP and smith["magic"] == expertise.NONE
    # history has no skill: Mage class + Mages Guild = deep; either alone = some
    assert mage["history"] == expertise.DEEP
    assert expertise.levels(npc(cls="Scholar"), rules)["history"] == expertise.SOME


def test_vanilla_smith_with_low_numbers_is_still_an_expert(profile):
    # Tun-Zeeus as the plugin read him in game (Nov 5, 2026).
    tun = CharacterSheet(
        name="Tun-Zeeus", race="Argonian", sex="male", class_name="Smith", location="The Dividing Line",
        skills={"Armorer": 38, "Blade": 42, "Blunt": 38, "Heavy Armor": 38, "Light Armor": 30,
                "Mercantile": 40, "Alteration": 6, "Mysticism": 10, "Alchemy": 10},
        major_skills=("Armorer", "Blade", "Blunt", "Heavy Armor", "Light Armor", "Hand to Hand", "Mercantile"))
    lv = expertise.levels(tun, profile.expertise)
    assert lv["smithing"] == expertise.DEEP and lv["combat"] == expertise.DEEP
    assert lv["magic"] == expertise.NONE
    # the same number on a skill that isn't a major one is only "some"
    other = CharacterSheet(name="X", race="Nord", sex="male", class_name="Commoner", location="Bruma",
                           skills={"Armorer": 38})
    assert expertise.levels(other, profile.expertise)["smithing"] == expertise.SOME


def test_note_says_what_is_beyond_them(profile):
    note = expertise.expertise_note(npc(**SMITH), profile.expertise)
    assert "smithing" in note and "deeply" in note
    assert "schools of magic" in note and "beyond you" in note


def test_no_skills_no_note(profile):
    assert expertise.expertise_note(npc(), profile.expertise) == ""


def test_the_library_loads_and_has_no_post_cutoff_years(profile):
    import re
    assert len(profile.lore.entries) >= 30
    for e in profile.lore.entries:
        for year in re.findall(r"3E (\d+)", e.text):
            assert int(year) <= 432 or e.quest, (e.id, year)
        assert "4E" not in e.text and "Fourth Era" not in e.text, e.id


def test_scholar_entries_need_deep_expertise(profile):
    lib = profile.lore
    q = "Tell me about the Camoran Usurper and the history of the emperors."
    mage_ids = {e.id for e in lib.select(npc(**MAGE), expertise.levels(npc(**MAGE), profile.expertise), q)}
    smith_ids = {e.id for e in lib.select(npc(**SMITH), expertise.levels(npc(**SMITH), profile.expertise), q)}
    assert "3e-camoran-usurper" in mage_ids
    assert "3e-camoran-usurper" not in smith_ids


def test_specialist_magic_entries_for_mages_only(profile):
    q = "What's the difference between Alteration and Mysticism?"
    for who, expect in ((MAGE, True), (SMITH, False)):
        sheet = npc(**who)
        ids = {e.id for e in profile.lore.select(sheet, expertise.levels(sheet, profile.expertise), q)}
        assert ("magic-mysticism" in ids) is expect
        assert "magic-schools-overview" in ids          # everyone knows the six schools exist


def test_local_entries_only_for_locals(profile):
    q = "Where can I get a room and a meal?"
    local = npc(location="Three Sisters' Inn, in Leyawiin")
    away = npc(location="a tavern in Bruma")
    lv = expertise.levels(local, profile.expertise)
    assert "leyawiin-inns" in {e.id for e in profile.lore.select(local, lv, q)}
    assert "leyawiin-inns" not in {e.id for e in profile.lore.select(away, lv, q)}


def test_always_entries_and_quest_gating():
    lib = LoreLibrary([
        LoreEntry("summary", "You live in Testville.", (), "locals:Testville", always=True),
        LoreEntry("gated", "The gate opened.", ("gate",), quest="MQ05 50"),
        LoreEntry("open", "Gates are doors.", ("gate",)),
    ])
    sheet = npc(location="Testville")
    ids = [e.id for e in lib.select(sheet, {}, "Seen the gate?")]
    assert ids == ["summary", "open"]


def test_prompt_includes_note_and_lore(profile):
    sheet = npc(name="Harald", location="a smithy in Bruma", **SMITH)
    system = build_messages(sheet, profile, [], "How do you temper steel?")[0]["content"]
    assert "What you know:" in system and "smithing" in system
