"""Memory that lasts (Phase 5 step 2)."""

from __future__ import annotations

import asyncio
import json

from living_cyrodiil.game_chat import GameChat
from living_cyrodiil.memory import Memory
from living_cyrodiil.setting_profile import load_profile

from tests.test_game_chat import TIMING, FakeLLM, Recorder, said, sheet

WHO = {"name": "Tsrava"}


def test_exchanges_are_numbered_saved_and_read_back(tmp_path):
    m = Memory(tmp_path)
    told = []
    m.on_seq = lambda pid, seq: told.append((pid, seq))
    assert m.add("0003599A", WHO, "Hello", "Well met.") is None      # no playthrough yet: nothing saved
    m.set_playthrough("pt-1", 0)
    assert m.add("0003599A", WHO, "Hello", "Well met.") == 1
    assert m.add("0003599A", WHO, "Greeting", "Good day.", vanilla=True) == 2
    assert m.add("0001C8D9", {"name": "Other"}, "Hi", "Hm.") == 3
    assert told == [("pt-1", 1), ("pt-1", 2), ("pt-1", 3)]
    recent = m.recent("0003599A", 8)
    assert [(r.seq, r.player, r.npc, r.vanilla) for r in recent] == [
        (1, "Hello", "Well met.", False), (2, "Greeting", "Good day.", True)]
    data = json.loads((tmp_path / "pt-1" / "0003599A.json").read_text(encoding="utf-8"))
    assert data["npc"]["name"] == "Tsrava" and len(data["entries"]) == 2


def test_loading_an_older_save_sets_later_exchanges_aside(tmp_path):
    m = Memory(tmp_path)
    m.set_playthrough("pt-1", 0)
    for i in range(1, 6):
        m.add("0003599A", WHO, f"line {i}", f"reply {i}")
    # The save was made after exchange 3: 4 and 5 hadn't happened yet in it.
    m2 = Memory(tmp_path)
    m2.set_playthrough("pt-1", 3)
    assert [r.seq for r in m2.recent("0003599A", 8)] == [1, 2, 3]
    data = json.loads((tmp_path / "pt-1" / "0003599A.json").read_text(encoding="utf-8"))
    assert [e["seq"] for e in data["set_aside"]] == [4, 5]
    # New exchanges carry on from the save's number, so nothing clashes.
    assert m2.add("0003599A", WHO, "again", "Back so soon?") == 4
    assert [r.npc for r in m2.recent("0003599A", 2)] == ["reply 3", "Back so soon?"]


def test_playthroughs_never_mix(tmp_path):
    m = Memory(tmp_path)
    m.set_playthrough("pt-1", 0)
    m.add("0003599A", WHO, "Hello", "From the first character.")
    m.set_playthrough("pt-2", 0)
    assert m.recent("0003599A", 8) == []


def test_a_new_visit_starts_from_what_the_npc_remembers(tmp_path):
    async def scenario():
        mem = Memory(tmp_path)
        mem.set_playthrough("pt-1", 0)
        rec = Recorder()
        llm = FakeLLM("Welcome back to Leyawiin, Mathias.", "You again.")
        chat = GameChat(load_profile("vanilla"), llm, send=rec.send, notify=rec.notify, memory=mem)
        await chat.answer(sheet(), said("My name is Mathias."), TIMING)
        assert [r.player for r in mem.recent("0003599A", 8)] == ["My name is Mathias."]

        # Quit and load the save later: a fresh companion state, same files.
        chat.set_playthrough({"id": "pt-1", "seq": 1, "save": "Save 16"})
        await chat.answer(sheet(), said("Remember me?"), TIMING)
        prompt = " ".join(m["content"] for m in llm.calls[-1])
        assert "(earlier visit) My name is Mathias." in prompt and "Welcome back to Leyawiin, Mathias." in prompt
        assert "You have met this traveller before" in prompt
        assert [r.seq for r in mem.recent("0003599A", 8)] == [1, 2]

    asyncio.run(scenario())


def test_an_unknown_memory_number_keeps_every_memory(tmp_path):
    # Nov 9: a save without a record read as memory 0 and set every memory aside.
    m = Memory(tmp_path)
    m.set_playthrough("char-1", 0)
    for i in range(1, 4):
        m.add("0003599A", WHO, f"line {i}", f"reply {i}")
    told = []
    m2 = Memory(tmp_path)
    m2.on_seq = lambda pid, seq: told.append((pid, seq))
    m2.set_playthrough("char-1", -1)
    assert [r.seq for r in m2.recent("0003599A", 8)] == [1, 2, 3]
    assert told == [("char-1", 3)]                      # the game hears where numbering stands
    assert m2.add("0003599A", WHO, "again", "Hello again.") == 4


def test_game_chat_reads_a_missing_or_unknown_number_as_unknown(tmp_path):
    m = Memory(tmp_path)
    m.set_playthrough("char-1", 0)
    m.add("0003599A", WHO, "Hello", "Well met.")
    chat = GameChat(load_profile("vanilla"), FakeLLM(), send=Recorder().send, notify=Recorder().notify, memory=m)
    chat.set_playthrough({"id": "char-1", "seq": -1})
    assert len(m.recent("0003599A", 8)) == 1
    chat.set_playthrough({"id": "char-1"})                # no number at all
    assert len(m.recent("0003599A", 8)) == 1
