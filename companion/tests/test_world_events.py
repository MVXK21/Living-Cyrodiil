"""Quest-stage awareness (step 3b): triggers, who knows what, news spreading."""

from living_cyrodiil import world_events
from living_cyrodiil.character import CharacterSheet, FactionMembership
from living_cyrodiil.lore import LoreLibrary
from living_cyrodiil.paths import DATA_DIR
from living_cyrodiil.prompt import build_system_prompt
from living_cyrodiil.setting_profile import load_profile
from living_cyrodiil.world_events import Event, WorldEvents, check

STATE = {"quests": {"MQ06": {"stage": 100, "done": [10, 50, 100], "completed": True},
                    "MQ11": {"stage": 42, "done": [10, 21, 42]},
                    "MS48": {"stage": 90, "done": [5, 50, 90]}},
         "globals": {"KvatchDestroyed": 1}}


def test_triggers():
    assert check("global:KvatchDestroyed", STATE) and not check("global:SomethingElse", STATE)
    assert check("MQ06>=100", STATE) and check("MQ06:completed", STATE) and not check("MQ06<50", STATE)
    assert check("MQ11:done:42", STATE) and not check("MQ11:done:40", STATE)
    assert check("MS48>=90 and global:KvatchDestroyed", STATE) and not check("MS48>=90 and MQ16>=10", STATE)
    assert not check("MQ16>=10", STATE) and check("MQ16<10", STATE)       # not started: stage 0
    assert not check("MQ06>=100", {}) and not check("MQ06>=100", None)


def sheet(location="Bravil", day=10.0, factions=(), cls="Commoner", state=STATE):
    return CharacterSheet(name="x", race="Imperial", sex="male", class_name=cls, location=location,
                          factions=tuple(FactionMembership(f) for f in factions), days_passed=day, world_state=state)


def test_locals_know_at_once_others_after_three_days(tmp_path):
    ev = WorldEvents([Event("kvatch", "global:KvatchDestroyed", "Kvatch fell.", ("locals:Skingrad", "class:Guard"))])
    ev.use_playthrough(tmp_path)
    assert [e.id for e in ev.known_by(sheet("Skingrad", day=10))] == ["kvatch"]
    assert ev.known_by(sheet("Bravil", day=11)) == []
    assert [e.id for e in ev.known_by(sheet("Bravil", day=12, cls="Guard"))] == ["kvatch"]
    assert [e.id for e in ev.known_by(sheet("Bravil", day=13.5))] == ["kvatch"]
    # Kept per playthrough: a new tracker reads the first-seen day back.
    again = WorldEvents(ev.events)
    again.use_playthrough(tmp_path)
    assert [e.id for e in again.known_by(sheet("Bravil", day=13.1))] == ["kvatch"]


def test_private_news_and_ended_news_and_older_saves(tmp_path):
    ev = WorldEvents([Event("secret", "MQ06:completed", "Only mages know.", ("faction:Mages Guild",), only=True),
                      Event("over", "MQ06:completed", "Old news.", ("everyone",), ended="MQ11>=40")])
    ev.use_playthrough(tmp_path)
    assert ev.known_by(sheet(day=50)) == []                                   # private; "over" has ended
    assert [e.id for e in ev.known_by(sheet(day=50, factions=["Mages Guild"]))] == ["secret"]
    ev.known_by(sheet(day=50, state={"quests": {}, "globals": {}}))           # an older save: forgotten
    assert "secret" not in ev._seen


def test_the_shipped_events_load_and_their_triggers_read():
    events, days = world_events.load(DATA_DIR / "quest_events.toml")
    assert days == 3 and len(events) >= 40 and len({e.id for e in events}) == len(events)
    for e in events:
        check(e.trigger, STATE)            # no "can't read the trigger" for any of them
        assert e.fact.endswith((".", "!")), e.id


def test_news_reaches_the_prompt_and_replaces_world_facts(monkeypatch, tmp_path):
    ev = WorldEvents(*world_events.load(DATA_DIR / "quest_events.toml"))
    ev.use_playthrough(tmp_path)
    monkeypatch.setattr(world_events, "tracker", ev)
    state = {"quests": {"MQ14": {"stage": 50, "done": [50]}, "MS48": {"stage": 100, "done": [90]}},
             "globals": {"KvatchDestroyed": 1}}
    profile = load_profile("vanilla")
    first = build_system_prompt(sheet("Bruma", day=100, state=state), profile)
    assert "News and talk you've heard" in first and "Martin Septim" in first      # Bruma's own news, at once
    assert "Septim line seems to have ended" not in first
    assert "Kvatch has been destroyed" not in first and "Hero of Kvatch" not in first   # not here yet
    later = build_system_prompt(sheet("Bruma", day=104, state=state), profile)
    assert "Kvatch has been destroyed" in later and "You recognise the traveller" in later


def test_kvatch_lore_follows_the_global():
    lib = LoreLibrary.load(DATA_DIR / "lore")
    standing = sheet("Kvatch", state={"quests": {}, "globals": {}})
    fallen = sheet("Kvatch", state={"quests": {}, "globals": {"KvatchDestroyed": 1}})
    ids = lambda s: [e.id for e in lib.select(s, {}, "what's happened to kvatch, and the count?")]
    assert "kvatch-local-summary" in ids(standing) and "kvatch-fallen" not in ids(standing)
    assert "kvatch-fallen" in ids(fallen) and "kvatch-local-summary" not in ids(fallen)
    old_plugin = sheet("Kvatch", state={})
    assert "kvatch-local-summary" in ids(old_plugin)
