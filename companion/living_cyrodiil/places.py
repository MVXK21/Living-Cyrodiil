"""Names a place from a position, using the game's own map markers (lore
pass, Nov 10, 2026: the Imperial City Waterfront and the open country have no
names in the game data, so NPCs there were told only "the open country").

The markers (name, kind, position, world) are read once from the player's own
Oblivion.esm and cached in companion\\cache\\. The plugin (0.19.0) sends the
NPC's position outdoors, or the player's last outdoor position for an
interior, and the world's form id.
"""

from __future__ import annotations

import json
import logging
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .paths import CACHE_DIR

log = logging.getLogger(__name__)

CACHE = CACHE_DIR / "map_markers_v2.json"
MAP_MARKER = 0x10          # the MapMarker static every marker reference uses
CITY, TAVERN, SETTLEMENT, LANDMARK = 3, 8, 9, 7
NAMED_KINDS = {CITY, TAVERN, SETTLEMENT, LANDMARK}

AT_UNITS = 4000            # this close to a city, village or inn marker: you're at it
# Cities and districts sprawl: MVXK21 (Nov 12) met Umriel Talouse in the
# Waterfront's shacks 4,718 units from its marker on the docks, and she placed
# herself "a few hours' walk southwest of the Imperial City". The nearest
# marker still wins, so the stables just outside each city keep their names.
CITY_AT_UNITS = 6000
NEAR_UNITS = 12000         # this close to a village, inn or landmark: "near" it


@dataclass(frozen=True)
class Marker:
    world: int             # world form id, load order byte dropped
    name: str
    kind: int
    x: float
    y: float
    big: bool = False      # one of the walled cities (a reference point out in the country)


def scan(esm: Path) -> list[Marker]:
    data = esm.read_bytes()
    out: list[Marker] = []

    def subs(body: bytes):
        i = 0
        while i + 6 <= len(body):
            (n,) = struct.unpack_from("<H", body, i + 4)
            yield body[i:i + 4], body[i + 6:i + 6 + n]
            i += 6 + n

    worlds: dict[int, tuple[str, int]] = {}     # world id -> (name, parent world id)

    def walk(start: int, end: int, world: int) -> None:
        i = start
        while i < end:
            tag = data[i:i + 4]
            (size,) = struct.unpack_from("<I", data, i + 4)
            if tag == b"GRUP":
                label, gtype = struct.unpack_from("<Ii", data, i + 8)
                walk(i + 20, i + size, label if gtype == 1 else world)   # type 1: a world's children
                i += size
                continue
            if tag == b"WRLD":
                fid = struct.unpack_from("<I", data, i + 12)[0]
                sd = dict(subs(data[i + 20:i + 20 + size]))
                parent = struct.unpack("<I", sd[b"WNAM"][:4])[0] if len(sd.get(b"WNAM", b"")) >= 4 else 0
                worlds[fid & 0xFFFFFF] = (sd.get(b"FULL", b"").rstrip(b"\0").decode("cp1252", "replace"),
                                          parent & 0xFFFFFF)
            if tag == b"REFR":
                flags = struct.unpack_from("<I", data, i + 8)[0]
                body = data[i + 20:i + 20 + size]
                if flags & 0x00040000:
                    body = zlib.decompress(body[4:])
                sd = dict(subs(body))
                if b"XMRK" in sd and sd.get(b"NAME", b"")[:4] == struct.pack("<I", MAP_MARKER) and b"DATA" in sd:
                    x, y = struct.unpack_from("<ff", sd[b"DATA"])
                    name = sd.get(b"FULL", b"").rstrip(b"\0").decode("cp1252", "replace")
                    kind = sd.get(b"TNAM", b"\0")[0]
                    if name:
                        out.append(Marker(world & 0xFFFFFF, name, kind, x, y))
            i += 20 + size

    (header,) = struct.unpack_from("<I", data, 4)
    walk(20 + header, len(data), 0)

    # Walled cities are worlds of their own, with their markers (gates, the
    # castle) in the open world's coordinates. Each becomes one city point in
    # its parent world, at the middle of its markers, named after the city's
    # world; the Imperial City's districts make one "Imperial City".
    groups: dict[tuple[int, str], list[Marker]] = {}
    for m in out:
        name, parent = worlds.get(m.world, ("", 0))
        if parent and name and "test" not in name.lower():
            city = "Imperial City" if name.startswith("Imperial City") else name
            groups.setdefault((parent, city), []).append(m)
    for (parent, city), ms in groups.items():
        out.append(Marker(parent, city, CITY, sum(m.x for m in ms) / len(ms), sum(m.y for m in ms) / len(ms), True))
    # A city marker out in a top-level world (Kvatch) is a city too; castles,
    # lighthouses and the Waterfront belong to a city already listed.
    taken = {m.name for m in out if m.big}
    for i, m in enumerate(out):
        if (m.kind == CITY and not m.big and not worlds.get(m.world, ("", 0))[1] and m.name not in taken
                and not any(w in m.name.lower() for w in ("castle", "lighthouse", "waterfront", "imperial city"))):
            out[i] = Marker(m.world, m.name, m.kind, m.x, m.y, True)
    return out


def load(data_folder: Path | None) -> list[Marker]:
    esm = data_folder / "Oblivion.esm" if data_folder else None
    if esm is None or not esm.is_file():
        return []
    stamp = f"{esm.stat().st_size}-{int(esm.stat().st_mtime)}"
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return [Marker(*m) for m in cached["markers"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    markers = scan(esm)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"stamp": stamp, "markers": [[m.world, m.name, m.kind, m.x, m.y, m.big] for m in markers]}),
                     encoding="utf-8")
    log.info("Places: %d map markers read from Oblivion.esm", len(markers))
    return markers


def _walk(units: float) -> str:
    # The game's distances are compressed: the Imperial City to Chorrol (about
    # 95,000 units) is a few minutes' run, but people speak of it as most of
    # a day's walk. So distances are described on the world's own scale.
    if units < 12000:
        return "just outside"
    if units < 35000:
        return "a few hours' walk"
    if units < 80000:
        return "most of a day's walk"
    return "days of travel"


def _direction(dx: float, dy: float) -> str:
    # The game's +y is north and +x east.
    names = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]
    return names[int(((math.degrees(math.atan2(dy, dx)) + 360 + 22.5) % 360) // 45)]


@dataclass(frozen=True)
class Whereabouts:
    at: str = ""           # the place they're at, if any ("Imperial City Waterfront", "Weye")
    around: str = ""       # out in the country: "a short walk north of Chorrol, near Weynon Priory"


class Places:
    def __init__(self, markers: list[Marker]):
        self.markers = [m for m in markers if m.kind in NAMED_KINDS]

    def where(self, world_id: str, x: float, y: float) -> Whereabouts:
        try:
            world = int(world_id, 16) & 0xFFFFFF
        except (TypeError, ValueError):
            return Whereabouts()
        here = [(math.hypot(m.x - x, m.y - y), m) for m in self.markers if m.world == world]
        if not here:
            return Whereabouts()
        here.sort(key=lambda t: t[0])
        d, nearest = here[0]
        if (d <= AT_UNITS and nearest.kind in (SETTLEMENT, TAVERN)) or (d <= CITY_AT_UNITS and nearest.kind == CITY):
            return Whereabouts(at=nearest.name)
        cities = [(dist, m) for dist, m in here if m.big]
        parts = []
        if cities:
            cd, city = cities[0]
            name = "the Imperial City" if city.name == "Imperial City" else city.name
            way = _walk(cd)
            parts.append(f"{way} of {name}" if way == "just outside"
                         else f"{way} {_direction(x - city.x, y - city.y)} of {name}")
        small = [(dist, m) for dist, m in here if m.kind != CITY and dist <= NEAR_UNITS]
        if small:
            parts.append(f"near {small[0][1].name}")
        return Whereabouts(around=", ".join(parts))
