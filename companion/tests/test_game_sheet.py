"""Turning the plugin's talk_start character sheet into a CharacterSheet."""

from __future__ import annotations

from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.setting_profile import load_profile

# Shaped exactly like plugin/src/npc_sheet.cpp output (docs/protocol.md).
GAME_SHEET = {
    "ref_id": "0001A2B3", "base_id": "0001A2B2", "plugin": "Oblivion.esm",
    "name": "Witseidutsei", "race": "Argonian", "sex": "female", "class": "Publican",
    "factions": [
        {"name": "Leyawiin Merchants", "rank": 0, "rank_title": "Merchant"},
        {"name": "SE Hidden Plumbing", "rank": 0, "hidden": True},
    ],
    "disposition": 62, "interior": True, "cell": "Three Sisters' Inn",
    "location": "Three Sisters' Inn", "last_outdoor_place": "Leyawiin", "hour": 20.5,
}


def test_from_game_maps_fields():
    s = CharacterSheet.from_game(GAME_SHEET)
    assert (s.name, s.race, s.sex, s.class_name) == ("Witseidutsei", "Argonian", "female", "Publican")
    assert s.location == "Three Sisters' Inn, in Leyawiin"      # interior gets its city
    assert s.disposition == 62 and s.hour == 20.5 and s.interior
    assert s.ref_id == "0001A2B3" and s.plugin == "Oblivion.esm"


def test_hidden_factions_are_left_out():
    s = CharacterSheet.from_game(GAME_SHEET)
    assert [f.name for f in s.factions] == ["Leyawiin Merchants"]
    assert s.factions[0].rank_title == "Merchant"


def test_outdoors_uses_worldspace_and_no_city_suffix():
    s = CharacterSheet.from_game({"name": "Guard", "race": "Imperial", "sex": "male",
                                  "class": "Guard", "interior": False,
                                  "worldspace": "Leyawiin", "location": "Leyawiin",
                                  "last_outdoor_place": "Leyawiin"})
    assert s.location == "Leyawiin" and not s.interior


def test_interior_named_after_another_town_keeps_its_own_town():
    def sheet(location, outside):
        return CharacterSheet.from_game({"name": "Gerald Fander", "interior": True,
                                         "location": location, "last_outdoor_place": outside})
    assert sheet("Bravil Bayside Inn", "Leyawiin").location == "Bravil Bayside Inn"
    assert sheet("Three Sisters' Inn", "Leyawiin").location == "Three Sisters' Inn, in Leyawiin"
    assert sheet("Leyawiin Mages Guild", "Leyawiin").location == "Leyawiin Mages Guild"


def test_missing_disposition_leaves_mood_out_of_prompt():
    sheet = dict(GAME_SHEET)
    del sheet["disposition"]
    s = CharacterSheet.from_game(sheet)
    assert s.disposition is None
    prompt = build_system_prompt(s, load_profile())
    assert "neutral toward this person" not in prompt and "friendly toward" not in prompt


def test_odd_or_missing_fields_fall_back_safely():
    s = CharacterSheet.from_game({"factions": ["not a dict", {"rank": 2}], "hour": 3})
    assert s.name == "a stranger" and s.race and s.class_name and s.location == "Cyrodiil"
    assert s.factions == ()
    assert s.disposition is None


def test_summary_line():
    line = CharacterSheet.from_game(GAME_SHEET).summary()
    assert "Witseidutsei" in line and "Leyawiin Merchants (Merchant)" in line
    assert "disposition 62" in line and "20:30" in line and "Oblivion.esm" in line


def test_prompt_from_game_sheet():
    prompt = build_system_prompt(CharacterSheet.from_game(GAME_SHEET), load_profile())
    assert "You are Witseidutsei, a female Argonian publican in Three Sisters' Inn, in Leyawiin" in prompt
    assert "Leyawiin Merchants (Merchant)" in prompt
    assert "SE Hidden" not in prompt
    assert "Hist" in prompt            # Argonian speech note


def test_race_note_matches_mod_race_variants():
    profile = load_profile()
    khajiit = profile.race_note("Khajiit")
    assert khajiit and profile.race_note("Khajiit Cathay") == khajiit   # seen in MVXK21's list
    assert profile.race_note("dark elf") == profile.race_note("Dark Elf Ashlander")
    assert profile.race_note("Dremora") == ""                            # unknown stays empty


def test_where_the_npc_is():
    """Nov 5 playtest: Betto Plotius, on Leyawiin's streets, thought he was on the road."""
    from living_cyrodiil.prompt import describe_where
    street = dict(GAME_SHEET, interior=False, location="Leyawiin", in_town=True)
    assert "on the streets of Leyawiin itself, inside its walls" in describe_where(CharacterSheet.from_game(street))
    country = dict(GAME_SHEET, interior=False, location="", cell="", worldspace="")
    assert describe_where(CharacterSheet.from_game(country)) == "outdoors in the open country, away from any town"
    village = dict(GAME_SHEET, interior=False, location="Weye", in_town=False)
    assert describe_where(CharacterSheet.from_game(village)) == "outdoors at Weye"
    inside = dict(GAME_SHEET, interior=True, in_town=True)     # in_town only counts outdoors
    where = describe_where(CharacterSheet.from_game(inside))
    assert where.startswith("indoors, inside ") and "on the streets" not in where
    # Nov 9 playtest: Agata, in the Leyawiin Mages Guild, invited the player to come to the hall.
    guild = dict(GAME_SHEET, interior=True, location="Leyawiin Mages Guild")
    where = describe_where(CharacterSheet.from_game(guild))
    assert "inside Leyawiin Mages Guild itself" in where and "already in it" in where
