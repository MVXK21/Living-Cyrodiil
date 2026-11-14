"""Summaries and dates of earlier meetings (Phase 5 step 2c-d)."""

from __future__ import annotations

import asyncio
import dataclasses

from living_cyrodiil import recall
from living_cyrodiil.game_chat import GameChat
from living_cyrodiil.memory import Memory, Remembered
from living_cyrodiil.setting_profile import load_profile

from tests.test_game_chat import TIMING, FakeLLM, Recorder, said, sheet

WHO = {"name": "Agata"}


def test_how_long_ago_in_plain_words():
    ago = recall.describe_ago
    assert ago(None, 3.0) == "" and ago(3.0, None) == ""
    assert ago(10.05, 10.0) == "just a little while ago"
    assert ago(10.9, 10.3) == "earlier today"
    assert ago(11.2, 10.9) == "yesterday"
    assert ago(16.5, 10.5) == "6 days ago"
    assert ago(19.0, 10.0) == "about a week ago"
    assert ago(31.0, 10.0) == "about 3 weeks ago"
    assert ago(50.0, 10.0) == "about a month ago"
    assert ago(200.0, 10.0) == "about 6 months ago"
    assert ago(500.0, 10.0) == "more than a year ago"


def test_reading_the_summary_answer():
    text, facts = recall.parse_summary(
        "SUMMARY: The traveller, Mathias, asked to join the Guild; I sent him to Dagail.\n"
        "FACTS:\n- His name is Mathias\n- He worked as a mercenary in High Rock\n")
    assert text == "The traveller, Mathias, asked to join the Guild; I sent him to Dagail."
    assert facts == ["His name is Mathias", "He worked as a mercenary in High Rock"]
    assert recall.parse_summary("SUMMARY: We talked about the weather.\nFACTS:\n- none")[1] == []
    assert recall.parse_summary("We talked about the weather.") == ("We talked about the weather.", [])


def test_memory_note_lists_meetings_with_dates_and_newest_facts_win():
    summaries = [
        {"text": "We talked about the Guild.", "facts": ["His name is Mathias", "He is a mercenary"], "day": 4.5},
        {"text": "He came back to ask about Dagail.", "facts": ["his name is Mathias.", "He wants to join"],
         "day": 10.05},
    ]
    note = recall.memory_note(summaries, now=10.2)
    assert "- 6 days ago: We talked about the Guild." in note
    assert "- Earlier today: He came back to ask about Dagail." in note
    assert note.count("Mathias") == 1                     # the same fact once, the newer wording
    assert "his name is Mathias." in note and "He wants to join" in note
    assert recall.memory_note([], 10.0) == ""


def test_summaries_are_saved_found_and_set_aside_with_the_save(tmp_path):
    m = Memory(tmp_path)
    m.set_playthrough("pt-1", 0)
    for i in range(1, 4):
        m.add("000314E6", WHO, f"line {i}", f"reply {i}", day=10.0)
    todo = m.unsummarised("000314E6")
    assert [e.seq for e in todo] == [1, 2, 3]
    m.add_summary("000314E6", todo, "We talked.", ["His name is Mathias"], day=10.1)
    assert m.unsummarised("000314E6") == []
    m.add("000314E6", WHO, "line 4", "reply 4", day=12.0)
    assert [e.seq for e in m.unsummarised("000314E6")] == [4]
    assert m.summaries("000314E6", 6)[0]["text"] == "We talked."
    # A save made after exchange 2: the summary (covering 1-3) hadn't been written yet.
    m2 = Memory(tmp_path)
    m2.set_playthrough("pt-1", 2)
    assert m2.summaries("000314E6", 6) == []
    assert [e.seq for e in m2.unsummarised("000314E6")] == [1, 2]


def _chat(tmp_path, llm):
    mem = Memory(tmp_path)
    mem.set_playthrough("pt-1", 0)
    rec = Recorder()
    return GameChat(load_profile("vanilla"), llm, send=rec.send, notify=rec.notify, memory=mem), mem


def test_a_conversation_is_summarised_when_the_npc_is_let_go(tmp_path):
    async def scenario():
        llm = FakeLLM("Well met, Mathias.",
                      "SUMMARY: Mathias, back from High Rock, greeted me.\nFACTS:\n- His name is Mathias")
        chat, mem = _chat(tmp_path, llm)
        agata = dataclasses.replace(sheet(ref="000314E6", name="Agata"), days_passed=10.0)
        await chat.answer(agata, said("I'm Mathias, back from High Rock."), TIMING)
        assert mem.recent("000314E6", 8)[0].day == 10.0
        await chat.talk_over({"type": "talk_over", "ref_id": "000314E6", "reason": "conversation over"})
        assert mem.summaries("000314E6", 6)[0]["facts"] == ["His name is Mathias"]
        await chat.talk_over({"type": "talk_over", "ref_id": "000314E6"})     # nothing new: no second call
        assert len(llm.calls) == 2

    asyncio.run(scenario())


def test_talking_again_soon_carries_on_later_is_a_new_meeting(tmp_path):
    async def scenario():
        llm = FakeLLM("Hello.", "SUMMARY: He said hello.\nFACTS:\n- none", "As I was saying.",
                      "SUMMARY: He came back.\nFACTS:\n- none", "Back again, I see.")
        chat, mem = _chat(tmp_path, llm)
        agata = dataclasses.replace(sheet(ref="000314E6", name="Agata"), days_passed=10.0)
        await chat.answer(agata, said("Hello."), TIMING)
        await chat.talk_over({"ref_id": "000314E6"})
        # 20 game minutes later: the same conversation, no "earlier visit".
        await chat.answer(dataclasses.replace(agata, days_passed=10.014), said("One more thing."), TIMING)
        prompt = " ".join(m["content"] for m in llm.calls[-1])
        assert "earlier visit" not in prompt and "You have met this traveller before" not in prompt
        await chat.talk_over({"ref_id": "000314E6"})
        # Two game days later: a new meeting that recalls the others.
        await chat.answer(dataclasses.replace(agata, days_passed=12.0), said("Remember me?"), TIMING)
        prompt = " ".join(m["content"] for m in llm.calls[-1])
        assert "(earlier visit, 2 days ago) Hello." in prompt
        assert "What you remember of this traveller" in prompt and "He said hello." in prompt

    asyncio.run(scenario())


def test_a_failed_summary_keeps_the_exchanges(tmp_path):
    async def scenario():
        from living_cyrodiil.llm import LLMError
        llm = FakeLLM("Hello.", LLMError("busy"))
        chat, mem = _chat(tmp_path, llm)
        await chat.answer(sheet(ref="000314E6", name="Agata"), said("Hello."), TIMING)
        await chat.talk_over({"ref_id": "000314E6"})
        assert mem.summaries("000314E6", 6) == [] and len(mem.unsummarised("000314E6")) == 1

    asyncio.run(scenario())


def test_vanilla_lines_alone_are_not_summarised(tmp_path):
    m = Memory(tmp_path)
    m.set_playthrough("pt-1", 0)
    m.add("000314E6", WHO, "Mages Guild", "Welcome to the Guild.", vanilla=True)
    assert [e.vanilla for e in m.unsummarised("000314E6")] == [True]
    assert isinstance(m.unsummarised("000314E6")[0], Remembered)


def test_a_summary_adds_only_whats_new_and_nothing_new_is_still_covered(tmp_path):
    async def scenario():
        llm = FakeLLM("Hello, Mathias.",
                      "SUMMARY: Mathias greeted me.\nFACTS:\n- His name is Mathias",
                      "We spoke of the guild, as I recall.",
                      "SUMMARY: nothing new\nFACTS:\n- none")
        chat, mem = _chat(tmp_path, llm)
        agata = dataclasses.replace(sheet(ref="000314E6", name="Agata"), days_passed=10.0)
        await chat.answer(agata, said("I'm Mathias."), TIMING)
        await chat.talk_over({"ref_id": "000314E6"})
        await chat.answer(dataclasses.replace(agata, days_passed=12.0), said("What do you remember?"), TIMING)
        await chat.talk_over({"ref_id": "000314E6"})
        summary_prompt = llm.calls[-1][0]["content"]
        assert "What I already remember" in summary_prompt and "- Mathias greeted me." in summary_prompt
        assert [s["text"] for s in mem.summaries("000314E6", 6)] == ["Mathias greeted me."]
        assert mem.unsummarised("000314E6") == []          # the recap is covered, not summarised again

    asyncio.run(scenario())
