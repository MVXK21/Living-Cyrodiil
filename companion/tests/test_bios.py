"""Bios for key characters (lore pass, Nov 10), and the lore library's files."""

import tomllib

from living_cyrodiil.bios import Bio, Bios
from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.paths import DATA_DIR
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.setting_profile import load_profile

PROFILE = load_profile("vanilla")


def test_matching_by_name_with_or_without_titles_and_by_plugin():
    bios = Bios([Bio("Arriana Valga", "You rule Chorrol.", "Oblivion.esm"), Bio("Kellen", "You serve Stendarr.", "Knights.esp")])
    assert bios.for_npc("Countess Arriana Valga", "Oblivion.esm") == "You rule Chorrol."
    assert bios.for_npc("arriana  valga") == "You rule Chorrol."
    assert bios.for_npc("Arriana Valga", "SomeMod.esp") == ""        # a different NPC of the same name
    assert bios.for_npc("Kellen", "Knights.esp") and not bios.for_npc("Kellen", "Oblivion.esm")
    assert bios.for_npc("Nobody") == ""


def test_the_bio_reaches_the_prompt():
    s = CharacterSheet(name="Velus Hosidius", race="Imperial", sex="male", class_name="Publican",
                       location="The Merchants Inn, in Imperial City, Market District", plugin="Oblivion.esm")
    assert "Who you are: You are an Imperial innkeeper who runs the Merchants Inn" in build_system_prompt(s, PROFILE)
    other = CharacterSheet(name="Some Modded Guard", race="Imperial", sex="male", class_name="Guard", location="Bruma")
    assert "Who you are:" not in build_system_prompt(other, PROFILE)


def test_the_shipped_bios_load_and_are_unique():
    names = [n.lower() for n in PROFILE.bios.names()]
    assert len(PROFILE.bios) >= 100 and len(names) == len(set(names))


def test_bios_keep_secrets():
    # A bio shapes how the NPC talks, so it holds only their public side.
    for name in ("Janus Hassildor", "Falcar", "Agronak gro-Malog", "Jauffre", "Martin", "Jakben Imbel"):
        text = PROFILE.bios.for_npc(name, "Oblivion.esm").lower()
        assert text, name
        for secret in ("vampire", "necromancer", "necromancy is your", "blades", "heir", "septim blood", "springheel"):
            assert secret not in text, (name, secret)


def test_every_lore_file_is_well_formed():
    ids = set()
    for path in sorted((DATA_DIR / "lore").glob("*.toml")):
        for e in tomllib.loads(path.read_text(encoding="utf-8")).get("entry", []):
            assert e["id"] not in ids, e["id"]
            ids.add(e["id"])
            assert e["text"].strip() and e.get("keywords"), e["id"]
            assert all(k == k.lower() for k in e["keywords"]), e["id"]
            assert e.get("depth", "common") in ("common", "specialist", "scholar"), e["id"]
            kind = e.get("known_by", "everyone").split(":")[0]
            assert kind in ("everyone", "locals", "faction"), e["id"]
    assert len(ids) >= 180
