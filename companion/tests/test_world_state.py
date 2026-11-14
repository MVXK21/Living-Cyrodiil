"""The world right now and the player's standing (Phase 5 step 3a)."""

from __future__ import annotations

import dataclasses

from living_cyrodiil import world_state as ws
from living_cyrodiil.character import CharacterSheet, FactionMembership, PlayerStanding
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.setting_profile import load_profile

PROFILE = load_profile("vanilla")


def npc(**kw) -> CharacterSheet:
    base = CharacterSheet(name="Agata", race="Imperial", sex="female", class_name="Mage",
                          location="Leyawiin Mages Guild", interior=True)
    return dataclasses.replace(base, **kw)


def player(**kw) -> PlayerStanding:
    return PlayerStanding(**kw)


def test_date_and_weather():
    s = npc(day=27, month=7, year=433, weather="rain")
    assert ws.describe_date(s, PROFILE).startswith("Today is the 27th of Last Seed. ")
    assert '"the 27th of Last Seed"' in ws.describe_date(s, PROFILE)
    assert "don't add the year" in ws.describe_date(s, PROFILE)
    assert "Don't mention the date unless the traveller asks" in ws.describe_date(s, PROFILE)
    assert ws.describe_date(npc(day=1, month=0), PROFILE).startswith("Today is the 1st of Morning Star.")
    assert ws.describe_date(npc(day=12, month=11), PROFILE).startswith("Today is the 12th of Evening Star.")
    assert ws.describe_date(npc(), PROFILE) == ""
    assert ws.describe_weather(s) == "Outside, it's raining."
    assert ws.describe_weather(dataclasses.replace(s, interior=False, weather="snow")) == "It's snowing."
    assert ws.describe_weather(npc(weather="")) == ""


def test_guild_ranks_fellow_members_and_public_ranks_only():
    mages = FactionMembership("Mages Guild", "Apprentice")
    thieves = FactionMembership("Thieves Guild", "Bandit")
    standing = player(factions=(mages, thieves))
    # A fellow Mages Guild member knows the rank; not the Thieves Guild one.
    fellow = npc(factions=(FactionMembership("Mages Guild", "Evoker"),), player=standing)
    lines = ws.describe_standing(fellow, PROFILE)
    assert lines == ["The traveller is a fellow member of the Mages Guild (rank: Apprentice)."]
    # Anyone else knows nothing of either.
    assert ws.describe_standing(npc(player=standing), PROFILE) == []
    # Public-facing ranks are known to everyone (MVXK21, Nov 9).
    famous = player(factions=(FactionMembership("Mages Guild", "Arch-Mage"),
                              FactionMembership("Arena", "Grand Champion"),
                              FactionMembership("Fighters Guild", "Champion")))   # not public in the Fighters Guild
    lines = ws.describe_standing(npc(player=famous), PROFILE)
    assert "Everyone knows the traveller is the Arch-Mage of the Mages Guild." in lines
    assert "Everyone knows the traveller is the Grand Champion of the Arena." in lines
    assert not any("Fighters Guild" in line for line in lines)


def test_bounty_guards_always_others_when_large():
    guard = npc(name="Leyawiin City Watch", class_name="Leyawiin Guard", player=player(bounty=40))
    assert ws.describe_standing(guard, PROFILE) == [
        "As a guard, you know the traveller has a bounty of 40 gold on their head."]
    assert ws.describe_standing(npc(player=player(bounty=40)), PROFILE) == []
    wanted = ws.describe_standing(npc(player=player(bounty=1500)), PROFILE)
    assert wanted == ["Word is that the traveller is a wanted criminal with a large price on their head."]
    legion = npc(class_name="Soldier", factions=(FactionMembership("Imperial Legion", ""),), player=player(bounty=5))
    assert "bounty of 5 gold" in ws.describe_standing(legion, PROFILE)[0]


def test_reputation_as_rumours_by_level():
    assert ws.describe_standing(npc(player=player(fame=5, infamy=9)), PROFILE) == []
    assert ws.describe_standing(npc(player=player(fame=12)), PROFILE) == [ws.FAME[1]]
    assert ws.describe_standing(npc(player=player(fame=75, infamy=35)), PROFILE) == [ws.FAME[3], ws.INFAMY[2]]


def test_it_all_reaches_the_prompt_and_the_game_sheet_reads_it():
    sheet = CharacterSheet.from_game({
        "name": "Agata", "race": "Imperial", "sex": "female", "class": "Mage", "location": "Leyawiin Mages Guild",
        "interior": True, "day": 27, "month": 7, "year": 433, "weather": "cloudy",
        "factions": [{"name": "Mages Guild", "rank_title": "Evoker"}],
        "player": {"fame": 31, "infamy": 0, "bounty": 0,
                   "factions": [{"name": "Mages Guild", "rank_title": "Associate", "rank": 0}]},
    })
    assert sheet.player.factions[0].rank_title == "Associate"
    prompt = build_system_prompt(sheet, PROFILE)
    assert "Today is the 27th of Last Seed. " in prompt
    assert "unless someone asks for them. Outside, the sky is overcast." in prompt
    assert "fellow member of the Mages Guild (rank: Associate)" in prompt
    assert ws.FAME[2] in prompt
