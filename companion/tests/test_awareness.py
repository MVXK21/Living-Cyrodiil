"""NPC awareness, part 1 (step 4): what they're doing, what they see."""

from living_cyrodiil.awareness import describe, describe_activity, describe_player
from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.setting_profile import load_profile


def test_activity():
    # Nov 7 playtest: Emily Valerius, seated with a book, said she wasn't reading.
    assert describe_activity({"sitting": 3, "sleeping": 0, "package": 8, "reading": True, "using": "The Firmament"}) == \
        "When the traveller came up to you, you were sitting down and reading a book."
    assert "asleep" in describe_activity({"sleeping": 3, "sitting": 0})
    assert describe_activity({"package": 3, "sitting": 3}) == "When the traveller came up to you, you were sitting down and having a meal."
    assert describe_activity({"package": 8, "sitting": 0, "sleeping": 0}) == ""
    assert describe_activity({"package": -1, "sitting": -1}) == ""        # couldn't be read
    assert describe_activity({}) == ""


def test_what_they_see():
    assert describe_player({"weapon_out": 1, "health_pct": 100, "armor_rating": 70, "clothing_value": 0}) == \
        ["The traveller has a weapon drawn.", "The traveller is in heavy armour."]
    assert describe_player({"weapon_out": 0, "health_pct": 20, "armor_rating": 0, "clothing_value": 0}) == \
        ["The traveller is badly hurt and bleeding.", "The traveller is barely dressed, in nothing but their smallclothes."]
    assert describe_player({"weapon_out": 0, "health_pct": 100, "armor_rating": 5, "clothing_value": 40}) == []
    assert describe_player({"weapon_out": -1, "health_pct": -1, "armor_rating": -1, "clothing_value": -1}) == []


def test_it_reaches_the_prompt_from_the_game_sheet():
    sheet = CharacterSheet.from_game({"name": "Emily Valerius", "activity": {"sitting": 3, "reading": True},
                                      "player_seen": {"weapon_out": 1, "health_pct": 100}})
    lines = describe(sheet)
    assert lines[0].endswith("sitting down and reading a book.") and "weapon drawn" in lines[1]
    prompt = build_system_prompt(sheet, load_profile("vanilla"))
    assert "reading a book" in prompt and "React to it as someone like you would" in prompt
    assert describe(CharacterSheet.from_game({"name": "x"})) == []


def test_equipment():
    from living_cyrodiil.awareness import describe_equipment
    gear = [{"name": "Steel Bow", "kind": "bow"}, {"name": "Iron Arrows", "kind": "arrows"},
            {"name": "Fur Cuirass", "kind": "light armor"}, {"name": "Fur Boots", "kind": "light armor"},
            {"name": "Brown Shirt", "kind": "clothing"}, {"name": "", "kind": "bow"}, {"name": "Torch", "kind": "?"}]
    text = describe_equipment(gear)
    assert "bow: Steel Bow; arrows: Iron Arrows; light armour: Fur Cuirass, Fur Boots; clothing: Brown Shirt." in text
    assert "never deny it" in text and "drawn" not in text and "Torch" not in text
    assert describe_equipment(gear, 1).endswith("You have your Steel Bow drawn.")
    assert describe_equipment([]) == "" and describe_equipment(None) == ""
    sheet = CharacterSheet.from_game({"name": "Rhano", "equipment": gear, "activity": {"weapon_out": 0}})
    assert "Steel Bow" in build_system_prompt(sheet, load_profile("vanilla"))


def test_player_race_sex_and_gear():
    seen = {"sex": "female", "race": "Khajiit", "weapon_out": 1, "health_pct": 100, "armor_rating": 70,
            "clothing_value": 0,
            "equipment": [{"name": "Elven Longsword", "kind": "one-handed blade"},
                          {"name": "Steel Cuirass", "kind": "heavy armor"}]}
    facts = describe_player(seen)
    assert facts[0] == "The traveller is a Khajiit woman (so: she, her)."
    assert facts[1].startswith("The traveller has on: one-handed blade: Elven Longsword; heavy armour: Steel Cuirass. ")
    assert "The traveller has their Elven Longsword drawn." in facts
    assert "The traveller is in heavy armour." not in facts       # the items say it already
    assert describe_player({"sex": "male"})[0] == "The traveller is a man (so: he, him)."


def test_armour_comparison_and_no_example_gear():
    from living_cyrodiil.awareness import compare_armour
    mine = [{"name": "Fighters Guild Light Cuirass", "kind": "light armor"}]
    theirs = [{"name": "Steel Cuirass", "kind": "heavy armor"}, {"name": "Leather Boots", "kind": "light armor"}]
    assert compare_armour(mine, theirs).startswith(
        "In short: you are wearing light armour, and the traveller is wearing a mix of heavy and light armour.")
    assert compare_armour(mine, []) == ""
    sheet = CharacterSheet.from_game({"name": "Nahsi", "equipment": mine,
                                      "player_seen": {"equipment": [{"name": "Leather Cuirass", "kind": "light armor"}]}})
    prompt = build_system_prompt(sheet, load_profile("vanilla"))
    assert "the traveller is wearing light armour. Neither of you is more heavily armoured than the other. You know this for sure" in prompt
    # Example gear in the instructions was taken as fact (Nov 11 playtest).
    assert "steel armour and a mace" not in prompt and "my steel bow" not in prompt
