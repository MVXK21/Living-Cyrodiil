from living_cyrodiil.subtitles import MAX_CHARS, MAX_CHUNKS, split_into_chunks


def test_short_reply_is_one_chunk():
    assert split_into_chunks("Good evening, traveller.") == ["Good evening, traveller."]


def test_blank_reply_gives_no_chunks():
    assert split_into_chunks("   ") == []


def test_sentences_are_grouped_up_to_the_limit():
    text = ("The roads have been quiet this week. Too quiet, if you ask me. "
            "Three caravans came in from Bravil without a scratch. "
            "Either the bandits have moved on, or they're waiting for something bigger. "
            "I'd keep your sword close on the Green Road.")
    chunks = split_into_chunks(text)
    assert 2 <= len(chunks) <= MAX_CHUNKS
    assert all(len(c) <= MAX_CHARS for c in chunks)
    assert " ".join(chunks) == text                      # nothing lost or reordered
    assert all(c[-1] in ".!?" for c in chunks)           # split between sentences


def test_long_sentence_splits_at_a_comma():
    text = ("When the Emperor's guard came through Leyawiin last spring, every merchant on the waterfront "
            "closed their shutters, and the Count himself stood at the gate for most of the afternoon.")
    chunks = split_into_chunks(text)
    assert len(chunks) >= 2
    assert all(len(c) <= MAX_CHARS for c in chunks)
    assert chunks[0].endswith(",")
    assert " ".join(chunks) == text


def test_never_more_than_four_chunks():
    text = " ".join(f"This is sentence number {n}, and it goes on a little while." for n in range(20))
    chunks = split_into_chunks(text)
    assert len(chunks) == MAX_CHUNKS
    assert " ".join(chunks) == text


def test_word_split_when_no_punctuation():
    text = " ".join(["word"] * 60)          # 299 characters, no punctuation at all
    chunks = split_into_chunks(text)
    assert all(len(c) <= MAX_CHARS for c in chunks)
    assert " ".join(chunks) == text
