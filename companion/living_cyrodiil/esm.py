"""Read-only facts from the player's own Oblivion.esm (Phase 3 step 2):
which dialogue lines belong to one specific NPC, and which NPC each is.

A dialogue line (INFO) with a GetIsID condition is spoken only by the NPC it
names, so its voice files are that NPC's own voice actor. Lines without one
are generic: every NPC of that race and sex says them, in the race's voice.

The scan takes a few seconds and is cached in companion\\cache\\.
"""

from __future__ import annotations

import json
import logging
import struct
import zlib
from pathlib import Path

from .paths import CACHE_DIR

log = logging.getLogger(__name__)

CACHE = CACHE_DIR / "esm_speakers.json"
FUNC_GET_IS_ID = 72
_COMPRESSED = 0x00040000


def _subrecords(body: bytes):
    i = 0
    while i + 6 <= len(body):
        tag = body[i:i + 4]
        (n,) = struct.unpack_from("<H", body, i + 4)
        yield tag, body[i + 6:i + 6 + n]
        i += 6 + n


def _records(data: bytes, start: int, end: int, want: set[bytes]):
    i = start
    while i < end:
        tag = data[i:i + 4]
        (size,) = struct.unpack_from("<I", data, i + 4)
        if tag == b"GRUP":
            yield from _records(data, i + 20, i + size, want)
            i += size
            continue
        if tag in want:
            flags, form_id = struct.unpack_from("<II", data, i + 8)
            body = data[i + 20:i + 20 + size]
            if flags & _COMPRESSED:
                body = zlib.decompress(body[4:])
            yield tag, form_id, body
        i += 20 + size


def scan_speakers(esm: Path) -> dict[str, str]:
    """INFO form id (8 hex digits, mod index 00) -> the NPC base id (hex) it's
    conditioned on, for every line that only one NPC says."""
    data = esm.read_bytes()
    (header_size,) = struct.unpack_from("<I", data, 4)
    npcs = {fid for tag, fid, _ in _records(data, 20 + header_size, len(data), {b"NPC_"})}
    speakers: dict[str, str] = {}
    for tag, fid, body in _records(data, 20 + header_size, len(data), {b"INFO"}):
        for sub, payload in _subrecords(body):
            if sub == b"CTDA" and len(payload) >= 16:
                func, param = struct.unpack_from("<II", payload, 8)
                op = payload[0] >> 5
                # GetIsID <npc> == 1 (op 0, value 1.0): a line for that one NPC
                if func == FUNC_GET_IS_ID and param in npcs and op == 0 and struct.unpack_from("<f", payload, 4)[0] == 1.0:
                    speakers[f"{fid & 0xFFFFFF:08x}"] = f"{param:08X}"
                    break
    return speakers


def speakers(esm: Path) -> dict[str, str]:
    """scan_speakers, cached per Oblivion.esm (by size and date)."""
    stamp = f"{esm.stat().st_size}-{int(esm.stat().st_mtime)}"
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return cached["speakers"]
    except (OSError, ValueError, KeyError):
        pass
    found = scan_speakers(esm)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"stamp": stamp, "speakers": found}), encoding="utf-8")
    log.info("Oblivion.esm: %d dialogue lines belong to one specific NPC", len(found))
    return found


NPC_CACHE = CACHE_DIR / "esm_npc_voices.json"


def scan_npc_voices(esm: Path) -> dict[str, list]:
    """NPC base id (hex) -> [race name, voice race name, female] from the NPC
    and race records: the voice folder the game plays for them is the voice
    race's (if set) for their sex, else their own race's. Used when the live
    character sheet isn't available (the voice test tool)."""
    data = esm.read_bytes()
    (header_size,) = struct.unpack_from("<I", data, 4)
    races: dict[int, tuple[str, int, int]] = {}
    raw_npcs: list[tuple[int, int, bool]] = []
    for tag, fid, body in _records(data, 20 + header_size, len(data), {b"RACE", b"NPC_"}):
        subs = dict(_subrecords(body))
        if tag == b"RACE":
            vnam = subs.get(b"VNAM", b"")
            male, female = struct.unpack("<II", vnam) if len(vnam) == 8 else (0, 0)
            races[fid] = (subs.get(b"FULL", b"").rstrip(b"\0").decode("cp1252"), male, female)
        else:
            rnam, acbs = subs.get(b"RNAM", b""), subs.get(b"ACBS", b"")
            if len(rnam) == 4 and len(acbs) >= 4:
                raw_npcs.append((fid, struct.unpack("<I", rnam)[0], bool(struct.unpack_from("<I", acbs)[0] & 1)))
    out: dict[str, list] = {}
    for fid, race_id, female in raw_npcs:
        if race_id not in races:
            continue
        name, male_voice, female_voice = races[race_id]
        voice = races.get(female_voice if female else male_voice)
        out[f"{fid:08X}"] = [name, voice[0] if voice and voice[0] != name else "", female]
    return out


def npc_voices(esm: Path) -> dict[str, list]:
    """scan_npc_voices, cached per Oblivion.esm (by size and date)."""
    stamp = f"{esm.stat().st_size}-{int(esm.stat().st_mtime)}"
    try:
        cached = json.loads(NPC_CACHE.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return cached["npcs"]
    except (OSError, ValueError, KeyError):
        pass
    found = scan_npc_voices(esm)
    NPC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    NPC_CACHE.write_text(json.dumps({"stamp": stamp, "npcs": found}), encoding="utf-8")
    return found


def info_id(voice_file: str) -> str:
    """'ms16b_infogeneral_0002ba4d_1.mp3' -> '0002ba4d' (lower case)."""
    parts = voice_file.rsplit("\\", 1)[-1].split("_")
    return parts[-2].lower() if len(parts) >= 4 else ""
