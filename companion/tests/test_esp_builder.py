"""Reads the built LivingCyrodiil.esp back with an independent parser and
checks it has exactly the structure Oblivion's own records have."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build_esp  # noqa: E402


def parse_subs(body: bytes) -> list[tuple[str, bytes]]:
    out, i = [], 0
    while i < len(body):
        tag = body[i:i + 4].decode("ascii")
        (n,) = struct.unpack_from("<H", body, i + 4)
        out.append((tag, body[i + 6:i + 6 + n]))
        i += 6 + n
    assert i == len(body), "subrecords overrun the record"
    return out


def parse(data: bytes, start: int, end: int, path=()):
    """Yields (path-of-groups, tag, form_id, subrecords) for every record."""
    i = start
    while i < end:
        tag = data[i:i + 4].decode("ascii")
        if tag == "GRUP":
            size, label, gtype, _stamp = struct.unpack_from("<I4siI", data, i + 4)
            assert i + size <= end, "group overruns its parent"
            yield from parse(data, i + 20, i + size, path + ((label, gtype),))
            i += size
        else:
            size, flags, fid, _vc = struct.unpack_from("<IIII", data, i + 4)
            assert flags == 0
            yield path, tag, fid, parse_subs(data[i + 20:i + 20 + size])
            i += 20 + size
    assert i == end


def test_esp_structure(tmp_path):
    out = tmp_path / "LivingCyrodiil.esp"
    assert build_esp.main(["build_esp.py", str(out)]) == 0
    data = out.read_bytes()

    records = list(parse(data, 0, len(data)))
    tags = [(r[1], r[2]) for r in records]
    lines = [0x01000802, 0x01000804, 0x01000805, 0x01000806]     # reply parts 1, 2, 3, 4
    assert tags == ([("TES4", 0), ("GLOB", 0x01000803), ("DIAL", 0x01000801)]
                    + [("INFO", fid) for fid in lines] + [("QUST", 0x01000800)])

    header = dict(records[0][3])
    version, num, next_id = struct.unpack("<fiI", header["HEDR"])
    assert version == 1.0 and next_id == 0x809 and num == 11
    assert header["MAST"] == b"Oblivion.esm\0" and header["DATA"] == b"\0" * 8

    by_tag = {r[1]: r for r in records}
    assert by_tag["GLOB"][0] == ((b"GLOB", 0),)
    assert by_tag["DIAL"][0] == ((b"DIAL", 0),)
    assert by_tag["QUST"][0] == ((b"QUST", 0),)
    # Lines sit in the topic-children group labelled with the topic's id
    for r in records[3:7]:
        assert r[0] == ((b"DIAL", 0), (struct.pack("<I", 0x01000801), 7))

    glob = dict(by_tag["GLOB"][3])
    assert glob["EDID"] == b"LCPart\0" and glob["FNAM"] == b"s"

    dial = by_tag["DIAL"][3]
    assert [t for t, _ in dial] == ["EDID", "QSTI", "FULL", "DATA"]       # as vanilla DIAL
    assert dict(dial)["QSTI"] == struct.pack("<I", 0x01000800)
    assert dict(dial)["DATA"] == b"\x01"                                  # Conversation

    for part, r in enumerate(records[3:7], start=1):
        subs = r[3]
        assert [t for t, _ in subs] == ["DATA", "QSTI", "TRDT", "NAM1", "NAM2", "CTDA", "SCHR"]
        d = dict(subs)
        assert d["TRDT"][12] == 1                                         # one response each
        assert d["NAM1"] == f"(Living Cyrodiil placeholder {part})\0".encode()
        op, value, func, param1 = struct.unpack_from("<B3xfII", d["CTDA"])
        assert (op, value, func, param1) == (0x00, float(part), 74, 0x01000803)   # LCPart == part

    quest = dict(by_tag["QUST"][3])
    assert quest["EDID"] == b"LCQuest\0"
    assert quest["DATA"][0] & 0x01 and quest["DATA"][1] == 50              # start game enabled, priority


def test_build_is_reproducible(tmp_path):
    a, b = tmp_path / "a.esp", tmp_path / "b.esp"
    build_esp.main(["x", str(a)])
    build_esp.main(["x", str(b)])
    assert a.read_bytes() == b.read_bytes()
