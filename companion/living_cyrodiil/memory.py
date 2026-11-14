"""Memory that lasts (Phase 5 step 2, docs/phase5-plan.md).

Every exchange with an NPC (spoken, typed, or a line from the vanilla
dialogue window) is saved to that NPC's file for the current playthrough:

    companion/memory/<playthrough id>/<NPC ref id>.json

Plain JSON, so players can read, back up or delete it (MVXK21, Nov 7).

Which playthrough, and how much of its memory, comes from the plugin
(`playthrough`): the id and the memory number stored in the loaded save.
Each saved exchange takes the next number. Loading an older save means
anything numbered after it hadn't happened yet in that save: those entries
are set aside (kept in the file under "set_aside", never sent to the LLM),
so NPCs forget what was said after the save (MVXK21, Nov 7).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import USER_DIR

log = logging.getLogger(__name__)

MEMORY_DIR = USER_DIR / "memory"
FILE_VERSION = 1

SeqListener = Callable[[str, int], None]     # (playthrough id, newest memory number)


@dataclass(frozen=True)
class Remembered:
    seq: int
    player: str            # what the player said, or the vanilla topic
    npc: str               # what the NPC said
    vanilla: bool = False
    day: float | None = None


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80] or "unknown"


class Memory:
    def __init__(self, root: Path = MEMORY_DIR):
        self.root = root
        self.playthrough = ""
        self.seq = 0                       # the newest number in use
        self.on_seq: SeqListener | None = None
        self._lock = threading.Lock()      # replies are remembered from worker threads

    @property
    def active(self) -> bool:
        return bool(self.playthrough)

    @property
    def folder(self) -> Path | None:
        """The current playthrough's folder, or None."""
        return self._dir() if self.playthrough else None

    def _dir(self) -> Path:
        return self.root / _safe(self.playthrough)

    def _file(self, ref_id: str) -> Path:
        return self._dir() / f"{_safe(ref_id)}.json"

    def _read(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("Memory file %s couldn't be read (%s); starting it fresh", path.name, e)
        return {"version": FILE_VERSION, "npc": {}, "entries": [], "set_aside": []}

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    # -- which playthrough ----------------------------------------------------

    def set_playthrough(self, playthrough: str, seq: int, save: str = "") -> None:
        """A save was loaded. Entries numbered after `seq` hadn't happened in
        this save: they're set aside. A negative `seq` means the save's number
        isn't known (made before this feature, or renamed outside the game):
        then nothing is set aside, numbering carries on from the highest number
        used, and the game is told it, so its next save records it (Nov 9: an
        unknown number read as 0 once set every memory aside)."""
        self.playthrough = str(playthrough)
        known = int(seq) >= 0
        folder = self._dir()
        if not known:
            highest = 0
            if folder.is_dir():
                for path in folder.glob("*.json"):
                    data = self._read(path)
                    for e in data["entries"] + data.get("set_aside", []):
                        try:
                            highest = max(highest, int(e.get("seq", 0)))
                        except (TypeError, ValueError):
                            pass
            self.seq = highest
            log.info("Memory: playthrough %s, this save's memory number isn't known%s; keeping every "
                     "memory, carrying on from %d", self.playthrough, f" ({save})" if save else "", self.seq)
            if self.on_seq:
                try:
                    self.on_seq(self.playthrough, self.seq)
                except Exception:
                    log.exception("Memory: telling the game the memory number failed")
            return
        self.seq = int(seq)
        moved = files = 0
        if folder.is_dir():
            for path in folder.glob("*.json"):
                files += 1
                data = self._read(path)
                later = [e for e in data["entries"] if int(e.get("seq", 0)) > self.seq]
                # A summary covers exchanges up to its "seq_to": one reaching
                # past the save hadn't been written yet either.
                summaries = data.get("summaries", [])
                later_summaries = [s for s in summaries if int(s.get("seq_to", 0)) > self.seq]
                if later or later_summaries:
                    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    data["entries"] = [e for e in data["entries"] if int(e.get("seq", 0)) <= self.seq]
                    data["summaries"] = [s for s in summaries if int(s.get("seq_to", 0)) <= self.seq]
                    for e in later + later_summaries:
                        e["set_aside_at"] = stamp
                    data.setdefault("set_aside", []).extend(later)
                    data.setdefault("set_aside_summaries", []).extend(later_summaries)
                    self._write(path, data)
                    moved += len(later)
        log.info("Memory: playthrough %s at memory %d%s; %d NPC file(s)%s", self.playthrough, self.seq,
                 f" ({save})" if save else "", files,
                 f"; set aside {moved} later exchange(s) this save hadn't reached" if moved else "")

    # -- reading and writing -------------------------------------------------

    def recent(self, ref_id: str, count: int) -> list[Remembered]:
        """The last `count` exchanges with this NPC in this save."""
        if not self.active or not ref_id:
            return []
        data = self._read(self._file(ref_id))
        out = []
        for e in data["entries"][-count:]:
            try:
                out.append(Remembered(int(e["seq"]), str(e.get("player", "")), str(e.get("npc", "")),
                                      bool(e.get("vanilla", False)), e.get("day")))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def add(self, ref_id: str, npc: dict, player: str, said: str, vanilla: bool = False,
            day: float | None = None) -> int | None:
        """Saves one exchange; returns its number (None when there's no
        playthrough yet, for instance with an older plugin)."""
        if not self.active or not ref_id or not said:
            return None
        with self._lock:
            return self._add(ref_id, npc, player, said, vanilla, day)

    def _add(self, ref_id, npc, player, said, vanilla, day) -> int:
        self.seq += 1
        path = self._file(ref_id)
        data = self._read(path)
        data["version"] = FILE_VERSION
        data["npc"] = {k: v for k, v in npc.items() if v} or data.get("npc", {})
        entry = {"seq": self.seq, "player": player, "npc": said, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
        if vanilla:
            entry["vanilla"] = True
        if day is not None:
            entry["day"] = round(float(day), 2)
        data["entries"].append(entry)
        self._write(path, data)
        if self.on_seq:
            try:
                self.on_seq(self.playthrough, self.seq)
            except Exception:
                log.exception("Memory: telling the game the new memory number failed")
        return self.seq

    # -- summaries (Phase 5 step 2c) ------------------------------------------

    def unsummarised(self, ref_id: str) -> list[Remembered]:
        """Exchanges with this NPC that no summary covers yet."""
        if not self.active or not ref_id:
            return []
        data = self._read(self._file(ref_id))
        done = max([int(s.get("seq_to", 0)) for s in data.get("summaries", [])] + [0])
        out = []
        for e in data["entries"]:
            try:
                if int(e["seq"]) > done:
                    out.append(Remembered(int(e["seq"]), str(e.get("player", "")), str(e.get("npc", "")),
                                          bool(e.get("vanilla", False)), e.get("day")))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def add_summary(self, ref_id: str, covers: list[Remembered], text: str, facts: list[str],
                    day: float | None = None) -> None:
        """Saves the summary of one conversation (the exchanges in `covers`).
        An empty `text` (nothing new was said) still marks them as covered,
        so they aren't summarised again; it's never shown to the LLM."""
        if not self.active or not ref_id or not covers:
            return
        with self._lock:
            path = self._file(ref_id)
            data = self._read(path)
            summary = {"seq_from": covers[0].seq, "seq_to": covers[-1].seq, "text": text,
                       "facts": list(facts), "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            if day is None:
                day = covers[-1].day
            if day is not None:
                summary["day"] = round(float(day), 2)
            data.setdefault("summaries", []).append(summary)
            self._write(path, data)

    def summaries(self, ref_id: str, count: int) -> list[dict]:
        """The last `count` summaries of conversations with this NPC in this save."""
        if not self.active or not ref_id:
            return []
        data = self._read(self._file(ref_id))
        return [s for s in data.get("summaries", []) if s.get("text")][-count:]
