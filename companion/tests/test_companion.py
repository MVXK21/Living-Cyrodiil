r"""Offline tests for the companion. No network, no API key, no cost.

Run:  companion\.venv\Scripts\python.exe -m pytest companion\tests
"""

from __future__ import annotations

from pathlib import Path

import pytest

from living_cyrodiil.character import CharacterSheet, FactionMembership, load_sample_npcs
from living_cyrodiil.conversation import Conversation
from living_cyrodiil.llm import LLMResult
from living_cyrodiil.prompt import Turn, build_messages, build_system_prompt
from living_cyrodiil.reply_check import check_reply, clean_reply
from living_cyrodiil.settings import PLACEHOLDER_KEY, SettingsError, load_settings
from living_cyrodiil.setting_profile import load_profile


@pytest.fixture
def profile():
    return load_profile("vanilla")


@pytest.fixture
def khajiit():
    return CharacterSheet(
        name="Ri'shadra", race="Khajiit", sex="female", class_name="Publican",
        location="a tavern in Leyawiin", disposition=60, hour=21,
        factions=(FactionMembership("Thieves Guild", "Footpad"),),
    )


class FakeLLM:
    """Returns canned replies in order and records the prompts it was sent."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(messages)
        return LLMResult(text=self.replies.pop(0), seconds=0.01, model="fake")


# ---- data files -------------------------------------------------------------

def test_profile_and_samples_load(profile):
    assert profile.ground_rules and profile.world_facts and profile.banned_terms
    assert profile.race_note("KHAJIIT")          # case-insensitive
    npcs = load_sample_npcs()
    assert len(npcs) >= 3
    assert {n.race for n in npcs} >= {"Imperial", "Khajiit", "Argonian"}


# ---- prompt -----------------------------------------------------------------

def test_system_prompt_has_sheet_rules_and_race_voice(khajiit, profile):
    s = build_system_prompt(khajiit, profile)
    assert "Ri'shadra" in s and "khajiit" in s.lower() and "publican" in s
    assert "Leyawiin" in s and "3E 433" in s
    assert "Thieves Guild (Footpad)" in s
    assert "night" in s                                  # hour 21
    assert "do not know their name" in s                 # MVXK21: stranger by default
    assert "third person" in s                           # Khajiit speech note
    assert "Never more than four sentences" in s


def test_messages_order_and_correction(khajiit, profile):
    history = [Turn("Hello.", "Greetings, traveller.")]
    msgs = build_messages(khajiit, profile, history, "Any news?", correction="FIX THIS")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[-1]["content"] == "Any news?"
    assert msgs[0]["content"].endswith("FIX THIS")       # correction stays in the system message


# ---- reply cleaning and checking -------------------------------------------

def test_clean_reply_strips_actions_quotes_and_name():
    raw = 'Ri\'shadra: *smiles slyly* "Khajiit has heard things, yes." (She leans in.)'
    assert clean_reply(raw, "Ri'shadra") == "Khajiit has heard things, yes."


def test_clean_reply_trims_to_four_sentences():
    raw = "One. Two. Three. Four. Five. Six."
    assert clean_reply(raw) == "One. Two. Three. Four."


def test_banned_term_is_flagged(profile):
    r = check_reply("I left it on my computer.", "Marcus", profile.banned_terms)
    assert not r.ok and any("computer" in p for p in r.problems)


def test_banned_term_from_player_is_allowed(profile):
    r = check_reply("A smartphone? Never heard of such a thing.", "Marcus",
                    profile.banned_terms, player_line="What's a smartphone?")
    assert r.ok


def test_part_of_a_player_word_is_allowed(profile):
    r = check_reply("A smart phone? I know of no such thing.", "Marcus",
                    profile.banned_terms, player_line="What's a smartphone?")
    assert r.ok


def test_whole_words_only(profile):
    # "gun" must not match "begun"; "AI" must not match "said"
    r = check_reply("The festival has begun, she said.", "Marcus", profile.banned_terms)
    assert r.ok


def test_long_stage_directions_are_stripped():
    from living_cyrodiil.reply_check import clean_reply
    raw = ("*Harald looks up from the anvil, wiping soot from his brow with the back of a scarred "
           "hand, and sets the hammer down.* Stormcloaks? Never heard of them.")
    assert clean_reply(raw, "Hammer-Hand Harald") == "Stormcloaks? Never heard of them."


def test_other_games_slang_is_flagged(profile):
    for line in ("Welcome, outlander.", "Get out, n’wah.", "Quiet, milk-drinker.", "Rats? Skeevers, more like."):
        r = check_reply(line, "Marcus", profile.banned_for("Imperial"))
        assert not r.ok, line


def test_dunmer_words_only_for_dark_elves(profile):
    line = "Mind your tongue, s'wit, or I'll show you the door, fetcher."
    assert check_reply(line, "Dralasa", profile.banned_for("Dark Elf")).ok
    assert check_reply(line, "Dralasa", profile.banned_for("Dark Elf (Vampire)")).ok    # mod variant
    assert not check_reply(line, "Marcus", profile.banned_for("Imperial")).ok
    assert not check_reply("Well met, muthsera.", "Bjorn", profile.banned_for("Nord")).ok
    # Dark Elves still can't use the words vanilla never uses
    assert not check_reply("Welcome, outlander.", "Dralasa", profile.banned_for("Dark Elf")).ok


def test_ai_meta_talk_is_flagged(profile):
    r = check_reply("As an AI, I can't know that.", "Marcus", profile.banned_terms)
    assert not r.ok


# ---- conversation: retry and fallback --------------------------------------

def test_good_reply_first_time(khajiit, profile):
    llm = FakeLLM("Khajiit pours you a drink, friend.")
    info = Conversation(khajiit, profile, llm).reply("A drink, please.")
    assert info.text == "Khajiit pours you a drink, friend."
    assert info.attempts == 1 and not info.used_fallback


def test_bad_reply_is_retried_with_correction(khajiit, profile):
    llm = FakeLLM("Check the internet for that.", "Khajiit knows nothing of it.")
    info = Conversation(khajiit, profile, llm).reply("Where is Bravil?")
    assert info.attempts == 2 and info.text == "Khajiit knows nothing of it."
    assert "broke character" in llm.calls[1][0]["content"]


def test_fallback_after_two_bad_replies(khajiit, profile):
    llm = FakeLLM("Use your phone.", "Use your phone, I said.")
    info = Conversation(khajiit, profile, llm).reply("How do I get to Bravil?")
    assert info.used_fallback and info.text in profile.fallback_lines


def test_history_is_kept_and_capped(khajiit, profile):
    llm = FakeLLM(*[f"Reply {i}." for i in range(12)])
    convo = Conversation(khajiit, profile, llm)
    for i in range(12):
        convo.reply(f"Line {i}")
    assert len(convo.history) == 8
    assert convo.history[-1].npc == "Reply 11."


# ---- settings ---------------------------------------------------------------

def _write(tmp_path: Path, key: str) -> Path:
    p = tmp_path / "settings.local.toml"
    p.write_text(f'[llm]\napi_key = "{key}"\nmodel = "test/model"\n', encoding="utf-8")
    return p


def test_settings_reject_placeholder_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LC_LLM_API_KEY", raising=False)
    with pytest.raises(SettingsError, match="API key"):
        load_settings(_write(tmp_path, PLACEHOLDER_KEY))


def test_settings_missing_file(tmp_path):
    with pytest.raises(SettingsError, match="setup.ps1"):
        load_settings(tmp_path / "nope.toml")


def test_settings_env_key_wins_and_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_LLM_API_KEY", "sk-or-from-env")
    s = load_settings(_write(tmp_path, PLACEHOLDER_KEY))
    assert s.llm.api_key == "sk-or-from-env"
    assert s.llm.model == "test/model"
    assert "sk-or" not in repr(s.llm)
