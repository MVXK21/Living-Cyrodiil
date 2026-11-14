"""Step 6e: the player's transcript becomes the NPC's reply, sent to the game."""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.game_chat import GameChat
from living_cyrodiil.llm import LLMError, LLMResult
from living_cyrodiil.setting_profile import load_profile
from living_cyrodiil.stt import Transcript
from living_cyrodiil.talk import TurnTiming


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResult(text=reply, seconds=0.01, model="fake")


class Recorder:
    def __init__(self):
        self.sent: list[dict] = []
        self.notes: list[str] = []

    async def send(self, msg):
        self.sent.append(msg)
        return True

    async def notify(self, text):
        self.notes.append(text)
        return True


def sheet(ref="0003599A", name="Tsrava", disposition=55):
    return CharacterSheet(name=name, race="Khajiit", sex="female", class_name="Acrobat",
                          location="Leyawiin", disposition=disposition, ref_id=ref)


def said(text):
    return Transcript(text, 0.4, 2.0, "fake")


TIMING = TurnTiming(held=2.0, stt=0.4)


@pytest.fixture
def profile():
    return load_profile("vanilla")


def run(coro):
    return asyncio.run(coro)


def test_reply_is_sent_as_chunks(profile):
    out = Recorder()
    long_reply = ("Khajiit has walked these streets many years. The rain here never stops, friend. "
                  "Still, the fishermen say the catch was good this week, and that is something. "
                  "You should try the eel at the Five Claws Lodge, if your stomach is brave.")
    chat = GameChat(profile, FakeLLM(long_reply), out.send, out.notify)
    run(chat.answer(sheet(), said("How's life in Leyawiin?"), TIMING))
    assert len(out.sent) == 1
    msg = out.sent[0]
    assert msg["type"] == "reply" and msg["ref_id"] == "0003599A"
    assert msg["text"] == long_reply
    assert len(msg["chunks"]) >= 2 and " ".join(msg["chunks"]) == long_reply
    assert not out.notes


def test_each_npc_keeps_their_own_history(profile):
    out = Recorder()
    llm = FakeLLM("Greetings, stranger.", "Hail.", "As Khajiit said, greetings.")
    chat = GameChat(profile, llm, out.send, out.notify)
    run(chat.answer(sheet(), said("Hello there."), TIMING))
    run(chat.answer(sheet(ref="00012345", name="Guard"), said("Hello, guard."), TIMING))
    run(chat.answer(sheet(), said("What did you say before?"), TIMING))
    third_prompt = " ".join(m["content"] for m in llm.calls[2])
    assert "Hello there." in third_prompt and "Greetings, stranger." in third_prompt
    assert "Hello, guard." not in third_prompt          # the guard's conversation is separate


def test_sheet_is_refreshed_each_turn(profile):
    out = Recorder()
    chat = GameChat(profile, FakeLLM("Hm.", "Hm."), out.send, out.notify)
    run(chat.answer(sheet(disposition=55), said("Hi."), TIMING))
    run(chat.answer(sheet(disposition=90), said("Hi again."), TIMING))
    assert chat.conversation_for(sheet(disposition=90)).sheet.disposition == 90


def test_llm_error_is_shown_not_sent(profile):
    out = Recorder()
    chat = GameChat(profile, FakeLLM(LLMError("The API key was rejected.")), out.send, out.notify)
    run(chat.answer(sheet(), said("Hello."), TIMING))
    assert out.sent == []
    assert out.notes and "Tsrava couldn't answer" in out.notes[0] and "API key" in out.notes[0]


def test_forget_all_starts_fresh(profile):
    out = Recorder()
    llm = FakeLLM("First.", "Second.")
    chat = GameChat(profile, llm, out.send, out.notify)
    run(chat.answer(sheet(), said("Remember the word lantern."), TIMING))
    chat.forget_all("save_loaded")
    run(chat.answer(sheet(), said("What was the word?"), TIMING))
    assert "lantern" not in " ".join(m["content"] for m in llm.calls[1])


def vline(text, topic="", ref="0003599A"):
    return {"type": "vanilla_line", "ref_id": ref, "name": "Tsrava", "text": text, "topic": topic}


def test_vanilla_lines_join_the_history_in_order(profile):
    out = Recorder()
    llm = FakeLLM("Steel? The forge on Main Street.", "Ah, the People of the Root.", "Hm.")
    chat = GameChat(profile, llm, out.send, out.notify)
    run(chat.answer(sheet(), said("Where can I buy good steel?"), TIMING))      # an earlier voice exchange
    argonians = "We are the People of the Root, called Argonians by the warmbloods."
    chat.vanilla_line(vline(argonians, "What can you tell me about the Argonians?"))
    chat.vanilla_line(vline("In our native land, we hunt to live.", "What can you tell me about the Argonians?"))
    run(chat.answer(sheet(), said("Tell me more about that."), TIMING))
    msgs = llm.calls[1]
    # the vanilla exchange comes AFTER the steel exchange, just before "tell me more"
    assert msgs[-3]["content"] == "(In ordinary conversation, the traveller asks: What can you tell me about the Argonians?)"
    assert argonians in msgs[-2]["content"] and "hunt to live" in msgs[-2]["content"]   # one exchange
    assert msgs[-1]["content"] == "Tell me more about that."
    assert "Steel?" in msgs[-4]["content"]


def test_vanilla_lines_wait_for_a_conversation(profile):
    out = Recorder()
    llm = FakeLLM("Hm.", "Hm.")
    chat = GameChat(profile, llm, out.send, out.notify)
    greeting = "Welcome to the Five Claws."
    chat.vanilla_line(vline(greeting))                                 # greeting: no topic
    run(chat.answer(sheet(), said("Tell me more about that."), TIMING))
    msgs = llm.calls[0]
    assert msgs[1]["content"] == "(The traveller comes up to you in ordinary conversation.)"
    assert msgs[2]["content"] == greeting
    # another NPC doesn't get Tsrava's lines, and loading a save forgets them
    run(chat.answer(sheet(ref="00012345", name="Guard"), said("Hello."), TIMING))
    assert greeting not in " ".join(m["content"] for m in llm.calls[1])
    chat.forget_all("save_loaded")
    assert chat.conversation_for(sheet()).history == []


def test_turn_report(profile):
    out = Recorder()
    reports = []
    chat = GameChat(profile, FakeLLM("Well met."), out.send, out.notify)
    chat.on_turn = reports.append
    run(chat.answer(sheet(), said("Hello."), TIMING))
    (r,) = reports
    assert r.npc == "Tsrava" and r.player == "Hello." and r.reply == "Well met."
    assert r.stt == 0.4 and r.attempts == 1 and r.chunks == 1 and r.total >= r.llm


def test_vanilla_lines_are_stated_as_the_npcs_own_words(profile):
    """Nov 5 playtest: Numeen disowned her own vanilla news. Her lines now go
    into the system prompt as her own words, after the general lore."""
    from living_cyrodiil.prompt import Turn, build_messages, clean_topic
    assert clean_topic("1. Tell the guards.") == "Tell the guards"
    assert clean_topic("3) Husband") == "Husband"
    assert clean_topic("What can you tell me about the Argonians?") == "What can you tell me about the Argonians?"
    history = [Turn("", "The settlement of Blankenmarch is being attacked by a band of pirates.", vanilla=True),
               Turn("1. Band of pirates", "I told you. They're at Blankenmarch. Do something!", vanilla=True)]
    msgs = build_messages(sheet(), profile, history, "Surely you know something about Blankenmarch?")
    system = msgs[0]["content"]
    said = system.index("A moment ago you said this")
    assert "- (asked about Band of pirates) I told you. They're at Blankenmarch. Do something!" in system
    assert "never take them back" in system
    if "Things you know that may help" in system:
        assert system.index("Things you know that may help") < said
    assert msgs[3]["content"] == "(In ordinary conversation, the traveller asks: Band of pirates)"


class _SlowLLM(FakeLLM):
    def __init__(self, delay, *replies):
        super().__init__(*replies)
        self.delay = delay

    def complete(self, messages):
        import time
        time.sleep(self.delay)
        return super().complete(messages)


class _FakeVoice:
    """Stands in for ReplyVoice: records fillers and voicing."""
    def __init__(self, filler_after):
        from living_cyrodiil.settings import VoiceSettings
        self.settings = VoiceSettings(filler_after_seconds=filler_after)
        self.ready = True
        self.turns = 0
        self.spoken: list[list[str]] = []

    def new_turn(self):
        self.turns += 1
        return self.turns

    async def send_filler(self, sheet, send):
        await send({"type": "reply", "ref_id": sheet.ref_id, "text": "Hmm.", "chunks": ["Hmm."],
                    "voiced": True, "turn": self.new_turn(), "filler": True})
        return True

    async def speak(self, sheet, chunks, send, turn=0):
        self.spoken.append(chunks)
        return 0.0


def test_slow_reply_gets_a_filler_first(profile):
    rec = Recorder()
    voice = _FakeVoice(filler_after=0.3)
    chat = GameChat(profile, _SlowLLM(0.8, "Well met, traveller."), send=rec.send, notify=rec.notify, voice=voice)
    run(chat.answer(sheet(), said("Hello."), TurnTiming(held=2.0, stt=0.0)))
    replies = [m for m in rec.sent if m["type"] == "reply"]
    assert [m.get("filler", False) for m in replies] == [True, False]     # filler, then the real reply
    assert replies[1]["text"] == "Well met, traveller." and replies[1]["turn"] != replies[0]["turn"]
    assert voice.spoken == [["Well met, traveller."]]


def test_quick_reply_gets_no_filler(profile):
    rec = Recorder()
    voice = _FakeVoice(filler_after=1.0)
    chat = GameChat(profile, _SlowLLM(0.05, "Well met, traveller."), send=rec.send, notify=rec.notify, voice=voice)
    run(chat.answer(sheet(), said("Hello."), TurnTiming(held=2.0, stt=0.0)))
    run(asyncio.sleep(1.2))                    # the cancelled timer never fires later
    assert [m.get("filler", False) for m in rec.sent if m["type"] == "reply"] == [False]


def test_filler_off_when_set_to_zero(profile):
    rec = Recorder()
    chat = GameChat(profile, _SlowLLM(0.3, "Well met."), send=rec.send, notify=rec.notify, voice=_FakeVoice(0))
    run(chat.answer(sheet(), said("Hello."), TurnTiming(held=2.0, stt=0.0)))
    assert not any(m.get("filler") for m in rec.sent)
