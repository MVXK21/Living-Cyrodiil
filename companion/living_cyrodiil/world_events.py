"""Quest-stage awareness (Phase 5 step 3b): which world events have happened,
from the plugin's live quest report (plugin 0.21.0, sheet "world_state"), and
which of them this NPC knows about (MVXK21, Nov 7 and 9: locals and those
concerned at once, everyone else after 3 in-game days; some news stays private;
only what would reasonably be public). Events and triggers are in
data/quest_events.toml (docs/phase5-3b-events.md).

The first in-game day each event was seen is kept per playthrough, in the
memory folder, so news spreads from when it happened. An event no longer
active (an older save loaded) is forgotten, so it spreads afresh if it happens
again.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SPREAD_DAYS = 3.0


# ---- triggers ----------------------------------------------------------------------

_STAGE = re.compile(r"^(\w+)\s*(>=|<=|==|>|<)\s*(\d+)$")


def _one(cond: str, state: dict) -> bool:
    cond = cond.strip()
    quests = state.get("quests") if isinstance(state.get("quests"), dict) else {}
    globals_ = state.get("globals") if isinstance(state.get("globals"), dict) else {}
    if cond.lower().startswith("global:"):
        name = cond.split(":", 1)[1].strip().lower()
        return any(k.lower() == name and v for k, v in globals_.items())
    q = {k.lower(): v for k, v in quests.items()}
    parts = cond.split(":")
    if len(parts) == 3 and parts[1].lower() == "done":
        info = q.get(parts[0].lower(), {})
        return isinstance(info, dict) and int(parts[2]) in [int(x) for x in info.get("done", []) if isinstance(x, (int, float))]
    if len(parts) == 2 and parts[1].lower() == "completed":
        info = q.get(parts[0].lower(), {})
        return isinstance(info, dict) and bool(info.get("completed"))
    m = _STAGE.match(cond)
    if m:
        info = q.get(m.group(1).lower(), {})
        stage = info.get("stage", 0) if isinstance(info, dict) else 0
        n = int(m.group(3))
        return {">=": stage >= n, "<=": stage <= n, "==": stage == n, ">": stage > n, "<": stage < n}[m.group(2)]
    log.warning("World events: can't read the trigger %r", cond)
    return False


def check(trigger: str, state: dict | None) -> bool:
    """True if the trigger ("MQ06>=100 and global:KvatchDestroyed") holds."""
    if not trigger or not isinstance(state, dict):
        return False
    return all(_one(c, state) for c in re.split(r"\s+and\s+", trigger.strip()))


# ---- events ------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    id: str
    trigger: str
    fact: str
    who_first: tuple[str, ...] = ("everyone",)
    only: bool = False
    ended: str = ""
    about_player: bool = False
    hides_fact: str = ""


def load(path: Path) -> tuple[list[Event], float]:
    if not path.is_file():
        return [], SPREAD_DAYS
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    events = [Event(id=str(e["id"]), trigger=str(e["trigger"]), fact=" ".join(str(e["fact"]).split()),
                    who_first=tuple(str(w) for w in e.get("who_first", ["everyone"])), only=bool(e.get("only", False)),
                    ended=str(e.get("ended", "")), about_player=bool(e.get("about_player", False)),
                    hides_fact=str(e.get("hides_fact", "")))
              for e in data.get("event", []) if e.get("id") and e.get("trigger") and e.get("fact")]
    return events, float(data.get("spread_days", SPREAD_DAYS))


def _concerns(who: str, sheet) -> bool:
    kind, _, value = who.partition(":")
    value = value.strip().lower()
    if kind == "everyone":
        return True
    if kind == "locals":
        return bool(value) and value in str(getattr(sheet, "location", "")).lower()
    if kind == "faction":
        return any(f.name.strip().lower() == value for f in getattr(sheet, "factions", ()))
    if kind == "class":
        return str(getattr(sheet, "class_name", "")).strip().lower() == value
    return False


class WorldEvents:
    def __init__(self, events: list[Event], spread_days: float = SPREAD_DAYS):
        self.events = events
        self.spread_days = spread_days
        self._seen_path: Path | None = None
        self._seen: dict[str, float] = {}

    def use_playthrough(self, folder: Path | None) -> None:
        """Where the first-seen days are kept (the playthrough's memory folder)."""
        path = folder / "world_events.json" if folder else None
        if path == self._seen_path:
            return
        self._seen_path = path
        self._seen = {}
        if path and path.is_file():
            try:
                self._seen = {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
            except (OSError, ValueError, TypeError, AttributeError):
                self._seen = {}

    def _save(self) -> None:
        if self._seen_path:
            try:
                self._seen_path.parent.mkdir(parents=True, exist_ok=True)
                self._seen_path.write_text(json.dumps(self._seen, indent=1), encoding="utf-8")
            except OSError as e:
                log.warning("World events: couldn't save %s: %s", self._seen_path, e)

    def active(self, state: dict | None, day: float | None) -> list[tuple[Event, float]]:
        """Events happening now, each with how many days ago it was first seen.
        Updates the first-seen record."""
        if not isinstance(state, dict) or not state:
            return []
        out, changed = [], False
        for e in self.events:
            on = check(e.trigger, state) and not (e.ended and check(e.ended, state))
            if not on:
                if e.id in self._seen and not check(e.trigger, state):
                    del self._seen[e.id]       # an older save: it hasn't happened here
                    changed = True
                continue
            if e.id not in self._seen:
                self._seen[e.id] = day if day is not None else 0.0
                changed = True
                log.info("World events: %s is new (day %s)", e.id, day)
            ago = (day - self._seen[e.id]) if day is not None else 0.0
            out.append((e, max(0.0, ago)))
        if changed:
            self._save()
        return out

    def known_by(self, sheet) -> list[Event]:
        """The active events this NPC knows about."""
        known = []
        for e, ago in self.active(getattr(sheet, "world_state", None), getattr(sheet, "days_passed", None)):
            if any(_concerns(w, sheet) for w in e.who_first):
                known.append(e)
            elif not e.only and ago >= self.spread_days:
                known.append(e)
        return known


# The events the prompt uses, set by the companion at startup (None in tests and tools).
tracker: WorldEvents | None = None
