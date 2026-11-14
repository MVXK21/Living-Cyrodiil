"""Bios for key characters (Phase 5 step 5d, in the lore pass by MVXK21's
choice, Nov 10, 2026): who an NPC is, in a short paragraph, given to that NPC
themselves. Written in our own words from their UESP pages (CC BY-SA), with
no quoted dialogue, no quest spoilers and no secrets: a bio shapes how the
NPC talks, so it holds only their public side and their character.

Files: data/bios/*.toml, entries:

    [[bio]]
    name = "Velus Hosidius"          # the name the game gives them
    plugin = "Oblivion.esm"          # optional: only for the NPC from this plugin
    text = "..."
    crisis = "..."                   # optional: only in the default story's Oblivion Crisis
                                     # (left out when world knowledge replaces the world facts)

Matched by name (and plugin when given), so a mod that edits an NPC keeps
their bio, and a mod-added NPC simply has none.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# The game often puts a title in the name ("Countess Arriana Valga", "Brother
# Martin"); bios match with or without it.
_TITLES = ("high chancellor ", "countess ", "count ", "brother ", "sister ", "prior ", "captain ", "lady ", "lord ",
           "sir ", "arch-mage ", "archmage ")


def _key(name: str) -> str:
    n = " ".join(name.lower().split())
    for t in _TITLES:
        if n.startswith(t):
            return n[len(t):]
    return n


@dataclass(frozen=True)
class Bio:
    name: str
    text: str
    plugin: str = ""
    crisis: str = ""      # added only in the default story (the Oblivion Crisis)


class Bios:
    def __init__(self, bios: list[Bio]):
        self._by_name: dict[str, list[Bio]] = {}
        for b in bios:
            self._by_name.setdefault(_key(b.name), []).append(b)

    @staticmethod
    def load(folder: Path) -> "Bios":
        bios: list[Bio] = []
        if folder.is_dir():
            for path in sorted(folder.glob("*.toml")):
                try:
                    data = tomllib.loads(path.read_text(encoding="utf-8"))
                except (OSError, tomllib.TOMLDecodeError) as e:
                    log.warning("Bios: couldn't read %s: %s", path.name, e)
                    continue
                for raw in data.get("bio", []):
                    if raw.get("name") and raw.get("text"):
                        bios.append(Bio(str(raw["name"]).strip(), " ".join(str(raw["text"]).split()),
                                        str(raw.get("plugin", "")), " ".join(str(raw.get("crisis", "")).split())))
        log.info("Bios: %d loaded", len(bios))
        return Bios(bios)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_name.values())

    def names(self) -> list[str]:
        return [b.name for v in self._by_name.values() for b in v]

    def for_npc(self, name: str, plugin: str = "", own_world: bool = False) -> str:
        """The bio for this NPC, or "". `own_world`: the player's world
        knowledge replaces the world facts, so the bio's `crisis` part (the
        default story's Oblivion Crisis) is left out."""
        for b in self._by_name.get(_key(name), []):
            if not b.plugin or not plugin or b.plugin.lower() == plugin.lower():
                return b.text if own_world or not b.crisis else f"{b.text} {b.crisis}"
        return ""
