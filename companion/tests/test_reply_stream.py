"""Step 4b: replies streamed and voiced sentence by sentence."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.conversation import Conversation
from living_cyrodiil.game_chat import GameChat
from living_cyrodiil.llm import LLMError, LLMResult
from living_cyrodiil.reply_stream import SentenceSplitter, first_part_ready
from living_cyrodiil.setting_profile import load_profile
from living_cyrodiil.settings import VoiceSettings
from living_cyrodiil.stt import Transcript
from living_cyrodiil.talk import TurnTiming
from living_cyrodiil.voice_out import ReplyVoice
from living_cyrodiil.voices import VoiceChoice

import numpy as np

from living_cyrodiil.voices import REFERENCE_RATE as RATE


def _tone(seconds: float, level: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# -- the sentence splitter -----------------------------------------------------

def feed_all(text: str, size: int = 3) -> list[str]:
    s = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), size):
        out += s.feed(text[i:i + size])
    rest = s.flush()
    return out + ([rest] if rest else [])


def test_splits_sentences_as_they_arrive():
    s = SentenceSplitter()
    assert s.feed("Welcome to the Five Claws") == []
    assert s.feed(".") == []                           # can't tell yet: the end needs the space after it
    assert s.feed(" Sit down, friend! The eel") == ["Welcome to the Five Claws.", "Sit down, friend!"]
    assert s.flush() == "The eel"


def test_any_piece_size_gives_the_same_sentences():
    text = 'Well... I suppose so. "Mind the rain," she said. Is that all? Good day.'
    expected = ["Well...", "I suppose so.", '"Mind the rain," she said.', "Is that all?", "Good day."]
    for size in (1, 2, 5, 40):
        assert feed_all(text, size) == expected


def test_never_splits_inside_a_stage_direction():
    text = "*He sets down his mug. He sighs.* The roads are bad. (She laughs. Briefly.) Go well."
    assert feed_all(text, 4) == ["*He sets down his mug. He sighs.* The roads are bad.",
                                 "(She laughs. Briefly.) Go well."]


def test_closing_quotes_stay_with_their_sentence():
    assert feed_all('"Welcome, traveller." "Sit."', 2) == ['"Welcome, traveller."', '"Sit."']


def test_first_part_waits_for_a_short_opener():
    assert first_part_ready([], finished=False) is None
    assert first_part_ready(["Ah."], finished=False) is None
    assert first_part_ready(["Ah."], finished=True) == "Ah."
    assert first_part_ready(["Ah.", "The Mages Guild, you mean?"], finished=False) == "Ah. The Mages Guild, you mean?"
    long_first = "The rains came early this year, and the river has been rising ever since."
    assert first_part_ready([long_first], finished=False) == long_first
    assert first_part_ready(["Ah.", "x" * 160], finished=False) == "Ah."   # too long to join


# -- Conversation.reply_streaming -------------------------------------------------

class StreamLLM:
    """Streams each reply a few characters at a time."""
    def __init__(self, *replies, piece=4, delay=0.0, finish="stop"):
        self.replies = list(replies)
        self.piece, self.delay, self.finish = piece, delay, finish
        self.calls = 0
        self.read: list[str] = []

    def complete_streaming(self, messages, on_text):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        read, stopped = "", False
        for i in range(0, len(reply), self.piece):
            if self.delay:
                time.sleep(self.delay)
            piece = reply[i:i + self.piece]
            read += piece
            if not on_text(piece):
                stopped = True
                break
        self.read.append(read)
        return LLMResult(text=read, seconds=0.01, model="fake", finish_reason=None if stopped else self.finish,
                         stopped=stopped)


def npc(ref="0002C8D9"):
    return CharacterSheet(name="Witseidutsei", race="Argonian", sex="female", class_name="Publican",
                          location="Leyawiin", ref_id=ref, base_id="0002C8D8")


@pytest.fixture
def profile():
    return load_profile("vanilla")


def converse(profile, llm):
    firsts: list[str] = []
    convo = Conversation(npc(), profile, llm)
    return convo, firsts, (lambda text: firsts.append(text))


def test_part_one_is_the_first_sentence_and_the_rest_follows(profile):
    reply = ("The rains came early this year, friend. The river is high. "
             "Fishermen grumble, but they always do. Sit, and I will bring you something warm.")
    convo, firsts, on_first = converse(profile, StreamLLM(reply))
    info = convo.reply_streaming("How is Leyawiin?", on_first)
    assert firsts == ["The rains came early this year, friend."]
    assert info.parts[0] == firsts[0]
    assert " ".join(info.parts) == reply == info.text
    assert 2 <= len(info.parts) <= 4
    assert convo.history[-1].npc == reply


def test_part_one_is_sent_before_the_reply_is_finished(profile):
    reply = "The rains came early this year, friend. " + "Then the river rose, and rose again. " * 3
    llm = StreamLLM(reply, piece=4, delay=0.002)
    convo, _, _ = converse(profile, llm)
    seen_at: list[int] = []
    convo.reply_streaming("How is Leyawiin?", lambda text: seen_at.append(len("".join(llm.read)) or -1))
    # on_first_part ran while the stream was still going (nothing in llm.read yet).
    assert seen_at == [-1]


def test_stage_directions_are_dropped(profile):
    reply = "*Witseidutsei wipes the counter. She looks up.* Welcome to the Five Claws Lodge, traveller."
    convo, firsts, on_first = converse(profile, StreamLLM(reply))
    info = convo.reply_streaming("Hello.", on_first)
    assert firsts == ["Welcome to the Five Claws Lodge, traveller."]
    assert info.text == "Welcome to the Five Claws Lodge, traveller."


def test_a_bad_first_sentence_asks_again(profile):
    llm = StreamLLM("As an AI, I cannot say. But here is more.", "The Lodge serves eel stew, friend.")
    convo, firsts, on_first = converse(profile, llm)
    info = convo.reply_streaming("What's good here?", on_first)
    assert llm.calls == 2 and info.attempts == 2
    assert firsts == ["The Lodge serves eel stew, friend."]
    assert not llm.read[0].endswith("more.")                    # stopped reading at the bad sentence


def test_a_bad_later_sentence_ends_the_reply(profile):
    reply = "The Lodge serves eel stew, friend. It is cheap. As an AI I would know. And tasty."
    llm = StreamLLM(reply)
    convo, firsts, on_first = converse(profile, llm)
    info = convo.reply_streaming("What's good here?", on_first)
    assert llm.calls == 1
    assert info.text == "The Lodge serves eel stew, friend. It is cheap."
    assert info.cut_short == "a later sentence broke character"
    assert convo.history[-1].npc == info.text


def test_two_bad_replies_give_the_fallback_line(profile):
    llm = StreamLLM("As an AI, no.", "As an AI, still no.")
    convo, firsts, on_first = converse(profile, llm)
    info = convo.reply_streaming("Hello?", on_first)
    assert info.used_fallback and firsts == [info.text] and info.parts == [info.text]
    assert info.text in profile.fallback_lines


def test_short_opener_joins_the_next_sentence(profile):
    convo, firsts, on_first = converse(profile, StreamLLM("Ah. The Mages Guild, you mean? It is on the north side."))
    info = convo.reply_streaming("Where's the guild?", on_first)
    assert firsts == ["Ah. The Mages Guild, you mean?"]
    assert info.parts == ["Ah. The Mages Guild, you mean?", "It is on the north side."]


def test_one_short_sentence_is_the_whole_reply(profile):
    convo, firsts, on_first = converse(profile, StreamLLM("Go away."))
    info = convo.reply_streaming("Hello.", on_first)
    assert firsts == ["Go away."] and info.parts == ["Go away."]


def test_cut_off_end_is_dropped(profile):
    llm = StreamLLM("The Lodge serves eel stew, friend. And the best ale in", finish="length")
    convo, _, on_first = converse(profile, llm)
    assert convo.reply_streaming("What's good?", on_first).text == "The Lodge serves eel stew, friend."


def test_at_most_four_sentences(profile):
    llm = StreamLLM("One is here now. Two is here now. Three is here. Four is here. Five is here. Six.")
    convo, _, on_first = converse(profile, llm)
    info = convo.reply_streaming("Count.", on_first)
    assert info.text == "One is here now. Two is here now. Three is here. Four is here."
    assert not info.cut_short                    # the normal limit isn't reported as cut short


# -- GameChat sends part 1 early, then reply_parts, then each part's audio --------

class _FakeBank:
    def __init__(self):
        self.said: list[str] = []

    def say(self, choice, text):
        self.said.append(text)
        return _tone(0.5 + len(text) / 30, 0.3)


class _FakeLibrary:
    def choose(self, **kw):
        return VoiceChoice("oblivion.esm_argonian_f", "Argonian female", Path("ref.wav"))


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


def voice(tmp_path, filler_after=0.0):
    v = ReplyVoice(VoiceSettings(filler_after_seconds=filler_after), audio_dir=tmp_path)
    v.library, v.bank = _FakeLibrary(), _FakeBank()
    return v


def test_game_gets_part_one_then_the_parts_then_audio(profile, tmp_path):
    rec = Recorder()
    reply = ("The rains came early this year, friend. The river is high, and the docks are wet. "
             "Fishermen grumble, but they always do, so pay them no mind at all. Sit, and I will bring you "
             "something warm to drink.")
    chat = GameChat(profile, StreamLLM(reply), rec.send, rec.notify, voice=voice(tmp_path))
    asyncio.run(chat.answer(npc(), Transcript("How is Leyawiin?", 0.4, 2.0, "fake"), TurnTiming(2.0, 0.4)))
    types = [m["type"] for m in rec.sent]
    assert types[0] == "reply" and rec.sent[0]["streaming"] is True and rec.sent[0]["voiced"] is True
    assert rec.sent[0]["chunks"] == ["The rains came early this year, friend."]
    parts_msg = next(m for m in rec.sent if m["type"] == "reply_parts")
    assert parts_msg["chunks"][0] == rec.sent[0]["chunks"][0] and " ".join(parts_msg["chunks"]) == reply
    audio = [m for m in rec.sent if m["type"] == "reply_audio"]
    assert [m["part"] for m in audio] == list(range(1, len(parts_msg["chunks"]) + 1))
    assert all(m["turn"] == rec.sent[0]["turn"] == parts_msg["turn"] for m in audio)
    # reply_parts reaches the game before any later part's audio
    assert types.index("reply_parts") < [i for i, m in enumerate(rec.sent)
                                         if m["type"] == "reply_audio" and m["part"] == 2][0]
    assert not rec.notes


def test_llm_error_before_part_one_is_shown(profile, tmp_path):
    rec = Recorder()
    chat = GameChat(profile, StreamLLM(LLMError("No reply within 20 seconds.")), rec.send, rec.notify,
                    voice=voice(tmp_path))
    asyncio.run(chat.answer(npc(), Transcript("Hello?", 0.4, 2.0, "fake"), TurnTiming(2.0, 0.4)))
    assert not rec.sent and "couldn't answer" in rec.notes[0]


def test_slow_stream_gets_a_filler_first(profile, tmp_path):
    rec = Recorder()
    v = voice(tmp_path, filler_after=0.2)
    v.model_tag = "test"
    import living_cyrodiil.voice_out as vo
    old = vo.CACHE_DIR
    vo.CACHE_DIR = tmp_path / "cache"
    try:
        llm = StreamLLM("Well met, traveller, and welcome.", piece=2, delay=0.03)
        chat = GameChat(profile, llm, rec.send, rec.notify, voice=v)
        asyncio.run(chat.answer(npc(), Transcript("Hello.", 0.0, 2.0, "fake"), TurnTiming(2.0, 0.0)))
    finally:
        vo.CACHE_DIR = old
    replies = [m for m in rec.sent if m["type"] == "reply"]
    assert [m.get("filler", False) for m in replies] == [True, False]
