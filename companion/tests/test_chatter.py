"""Who NPCs were just talking to (plugin 0.18.0), and narrated actions."""

from living_cyrodiil import chatter_text
from living_cyrodiil.character import CharacterSheet, ChatterLine
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.reply_check import clean_reply, is_narration
from living_cyrodiil.setting_profile import load_profile

PROFILE = load_profile("vanilla")


def sheet(lines=(), **kw):
    return CharacterSheet(name="Shamada", race="Khajiit", sex="female", class_name="Commoner",
                          location="Leyawiin", recent_chatter=tuple(lines), **kw)


def test_the_npc_knows_who_they_were_talking_to_and_keeps_it_private():
    s = sheet([ChatterLine("other", "Peony", seconds_ago=20), ChatterLine("self", "Peony", seconds_ago=12)])
    note = chatter_text.describe(s)
    assert "you were talking with Peony" in note and "a moment ago" in note and "never deny it" in note
    assert "private conversation" in note and "don't repeat or make up" in note
    assert note in build_system_prompt(s, PROFILE)


def test_only_the_latest_partner_and_how_long_ago():
    note = chatter_text.describe(sheet([ChatterLine("self", "Old Friend", seconds_ago=170),
                                        ChatterLine("self", "Gemellus Axius", seconds_ago=90)]))
    assert "Gemellus Axius" in note and "Old Friend" not in note and "a few minutes ago" in note


def test_nothing_without_chatter():
    assert chatter_text.describe(sheet()) == ""
    assert "Just before the traveller came up" not in build_system_prompt(sheet(), PROFILE)


def test_the_sheet_reads_it():
    s = CharacterSheet.from_game({"name": "Shamada", "recent_chatter": [
        {"by": "self", "other_name": "Peony", "other_ref": "3D0085EE", "seconds_ago": 12,
         "plugin": "Oblivion.esm", "file": "X_Y_00000001_1"},       # 0.18.0/0.18.1 fields: ignored
        {"by": "other", "other_name": "Peony", "seconds_ago": 8},
        {"by": "other"},                                           # no name: skipped
        "junk",
    ]})
    assert s.recent_chatter == (ChatterLine("self", "Peony", "3D0085EE", 12), ChatterLine("other", "Peony", "", 8))
    assert CharacterSheet.from_game({"name": "x"}).recent_chatter == ()


def test_narrated_actions_are_dropped():
    # Nov 10 tests: both came through as dialogue.
    assert is_narration("Shamada's ears flick toward the traveller, and her tail gives a lazy curl.", "Shamada")
    assert is_narration("Shamada flicks an ear and gives the traveller a sidelong purr.", "Shamada")
    assert is_narration("She smiles at the traveller.", "Agata")
    reply = ("Shamada's ears flick toward the traveller, and her tail gives a lazy curl. "
             "That was a private matter between Peony and this one, friend.")
    assert clean_reply(reply, "Shamada") == "That was a private matter between Peony and this one, friend."


def test_speech_is_kept():
    # Khajiit speak of themselves by name; talk about others isn't narration.
    assert not is_narration("Shamada sees only one stranger before her, and this one has been talking to herself.", "Shamada")
    assert not is_narration("Shamada smiles when you bring her moon sugar.", "Shamada")
    assert not is_narration("She smiles too much, that one.", "Agata")
    assert not is_narration("He nods to everyone in the market, the old fool.", "Agata")
    assert not is_narration("The weather is fair today.", "Shamada")


def test_narration_with_at_you_and_a_stripped_name():
    from living_cyrodiil.reply_check import clean_reply, is_narration
    # Nov 11 replays of Nahsi (plugin 0.21.7).
    assert is_narration("Nahsi looks down at her own arms and chest, then back at you with a flick of her tail.", "Nahsi")
    assert is_narration("Nahsi looks at her own arms and chest, then at yours, and her ears flatten a little.", "Nahsi")
    assert clean_reply("Nahsi: glances down at herself, then back at the traveller, tail flicking. "
                       "You are mistaken, friend.", "Nahsi") == "You are mistaken, friend."
    assert not is_narration("Nahsi looks at you and thinks you are wrong.", "Nahsi")
    assert not is_narration("Nahsi is sure of what she wears.", "Nahsi")
