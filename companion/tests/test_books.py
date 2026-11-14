"""The game's own books as knowledge (step 5c)."""

from living_cyrodiil import books
from living_cyrodiil.books import Book, BookLibrary, clean
from living_cyrodiil.character import CharacterSheet
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.setting_profile import load_profile

TEXT = "Akavir means Dragon Land. " * 100
LIB = BookLibrary([
    Book("Mysterious Akavir", TEXT, copies=272),                     # popular
    Book("Brief History of the Empire, v 1", "The first volume. " * 100, copies=144),
    Book("Brief History of the Empire, v 3", "The third volume. " * 100, copies=140),
    Book("The Firmament", "Of the stars and their signs. " * 100, copies=8),   # not popular
])


def test_titles_are_found_with_or_without_articles_and_volumes():
    assert [b.title for b in LIB.mentioned("Have you read mysterious akavir?")] == ["Mysterious Akavir"]
    assert LIB.mentioned("I liked the Brief History of the Empire")[0].title.endswith("v 1")
    assert LIB.mentioned("Brief History of the Empire, volume 3?")[0].title.endswith("v 3")
    assert LIB.mentioned("nothing about books here") == []


def test_popular_books_are_common_knowledge_others_need_expertise():
    note = LIB.note({}, "What's in Mysterious Akavir?")
    assert "well-known book" in note and len(note) < 700
    assert LIB.note({}, "Have you read The Firmament?") == ""
    expert = LIB.note({"history": 1}, "Have you read The Firmament?")
    assert "read it closely" in expert and len(expert) > 1000


def test_book_text_is_cleaned():
    assert clean('<div align="center"><font face=1>Title<br>Line &amp; more</font></div>') == "Title Line & more"


def test_the_note_reaches_the_prompt(monkeypatch):
    monkeypatch.setattr(books, "library", LIB)
    s = CharacterSheet(name="Phintias", race="Imperial", sex="male", class_name="Bookseller", location="First Edition")
    assert "Mysterious Akavir" in build_system_prompt(s, load_profile("vanilla"), talk="Tell me of Mysterious Akavir")
