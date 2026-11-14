"""Naming places from the game's map markers, and the Imperial City's lore (lore pass, Nov 10)."""


from living_cyrodiil import character
from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.lore import LoreLibrary
from living_cyrodiil.paths import DATA_DIR
from living_cyrodiil.places import CITY, SETTLEMENT, TAVERN, Marker, Places, Whereabouts
from living_cyrodiil.prompt import build_system_prompt, describe_where
from living_cyrodiil.setting_profile import load_profile

TAMRIEL = 0x3C
MARKERS = [
    Marker(TAMRIEL, "Imperial City", CITY, 29509, 63371, True),
    Marker(TAMRIEL, "Chorrol", CITY, -63766, 95968, True),
    Marker(TAMRIEL, "Imperial City Waterfront", CITY, 17816, 44588),
    Marker(TAMRIEL, "Weynon Priory", SETTLEMENT, -56998, 85907),
    Marker(TAMRIEL, "Wawnet Inn", TAVERN, -2088, 62990),
    Marker(0x1234, "Somewhere Else", CITY, 0, 0, True),
]
PLACES = Places(MARKERS)
PROFILE = load_profile("vanilla")


def test_at_a_place_with_no_name_of_its_own():
    assert PLACES.where("0000003C", 17000, 44000) == Whereabouts(at="Imperial City Waterfront")
    assert PLACES.where("0100003C", -2000, 63000).at == "Wawnet Inn"     # load order byte ignored


def test_out_in_the_country():
    w = PLACES.where("0000003C", -57000, 80000)          # south of the priory, west of the Imperial City
    assert w.at == "" and "of Chorrol" in w.around and "near Weynon Priory" in w.around
    assert "Imperial City Waterfront" not in PLACES.where("0000003C", -20000, 30000).around
    assert PLACES.where("0000003C", 29000, 75000).around.startswith("just outside of the Imperial City")


def test_unknown_world_or_bad_id():
    assert PLACES.where("00099999", 0, 0) == Whereabouts()
    assert PLACES.where("nonsense", 0, 0) == Whereabouts()


def test_the_sheet_names_the_waterfront_and_the_country(monkeypatch):
    monkeypatch.setattr(character, "place_lookup", PLACES.where)
    outside = CharacterSheet.from_game({"name": "Ormil", "interior": False, "pos_x": 17000, "pos_y": 44000,
                                        "world_id": "0000003C"})
    assert outside.location == "Imperial City Waterfront"
    assert describe_where(outside) == "outdoors at Imperial City Waterfront"
    inn = CharacterSheet.from_game({"name": "Ormil", "interior": True, "location": "The Bloated Float Inn",
                                    "last_outdoor_x": 17200, "last_outdoor_y": 44300, "last_outdoor_world_id": "0000003C"})
    assert inn.location == "The Bloated Float Inn, in Imperial City Waterfront"
    road = CharacterSheet.from_game({"name": "Bandit", "interior": False, "pos_x": -57000, "pos_y": 80000,
                                     "world_id": "0000003C"})
    assert road.location == "Cyrodiil" and "near Weynon Priory" in road.surroundings
    assert describe_where(road).startswith("outdoors in the open country, ") and "of Chorrol" in describe_where(road)
    # In a walled town, the game's own name stands.
    town = CharacterSheet.from_game({"name": "x", "interior": False, "in_town": True, "location": "Chorrol",
                                     "pos_x": 0, "pos_y": 0, "world_id": "0003633E"})
    assert town.location == "Chorrol" and town.surroundings == ""


def _lore_for(location, question, **kw):
    lib = LoreLibrary.load(DATA_DIR / "lore")
    sheet = CharacterSheet(name="Velus Hosidius", race="Imperial", sex="male", class_name="Publican",
                           location=location, **kw)
    return [e.id for e in lib.select(sheet, {}, question)]     # no expertise: common knowledge only


def test_an_imperial_city_local_knows_their_city_and_district():
    ids = _lore_for("The Merchants Inn, in Imperial City, Market District", "where can I get a drink and a bed?")
    assert ids[:2] == ["ic-local-summary", "ic-market-local"]
    assert "ic-inns" in ids and not any(i.startswith("leyawiin") for i in ids)


def test_other_cities_dont_know_imperial_city_streets():
    ids = _lore_for("Leyawiin", "where can I get a drink and a bed?")
    assert "ic-inns" not in ids and "leyawiin-inns" in ids


def test_waterfront_locals_get_both_summaries():
    ids = _lore_for("The Bloated Float Inn, in Imperial City Waterfront", "what's this place like?")
    assert ids[:2] == ["ic-local-summary", "ic-waterfront-local"]


def test_the_prompt_mentions_the_imperial_city_lore():
    s = CharacterSheet(name="Velus Hosidius", race="Imperial", sex="male", class_name="Publican",
                       location="The Merchants Inn, in Imperial City, Market District")
    prompt = build_system_prompt(s, PROFILE, talk="Is there a good smith around here?")
    assert "A Fighting Chance" in prompt and "Market District" in prompt


def test_waterfront_shacks_count_as_the_waterfront():
    # Nov 12 playtest: Umriel Talouse, 4,718 units from the Waterfront marker.
    assert PLACES.where("0000003C", 14273.41, 47704.01) == Whereabouts(at="Imperial City Waterfront")
