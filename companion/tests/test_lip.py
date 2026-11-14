"""Oblivion .lip files (Phase 4)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from living_cyrodiil import lip


def test_round_trip_and_header():
    w = lip.test_pattern(2.0)
    data = lip.encode(w)
    version, size, packed = struct.unpack_from("<3I", data, 0)
    assert (version, size, packed) == (1, 24 + 132 * len(w), 1)
    back, offset = lip.decode(data)
    assert offset == 0 and back.shape == (60, 33)
    assert np.array_equal(back, w)
    assert len(data) < size                       # zero runs packed


def test_matches_a_vanilla_file_byte_for_byte():
    # The start of a vanilla file (ms14_goodbye_000093ac_1.lip): 26 frames,
    # offset -8, then BMP (channel 2) rising in frames 1 and 2. Packing those
    # values alone must give the game's exact bytes.
    w = np.zeros((26, 33), np.float32)
    w[1, 2] = struct.unpack("<f", bytes.fromhex("a4610d3d"))[0]
    w[2, 2] = struct.unpack("<f", bytes.fromhex("6455013e"))[0]
    data = lip.encode(w, offset=-8)
    expected_start = bytes.fromhex("01000000 800d0000 01000000 1a 000300 f8ffffff 008c00 a4610d3d 008000 6455013e".replace(" ", ""))
    assert data == expected_start


def test_long_zero_runs_split():
    w = np.zeros((600, 33), np.float32)          # 79,200 zero bytes, more than one run holds
    w[-1, 0] = 0.5
    back, _ = lip.decode(lip.encode(w))
    assert np.array_equal(back, w)


def test_test_pattern_moves_jaw_and_blinks():
    w = lip.test_pattern(1.0)
    jaw = w[:, lip.CHANNELS["BigAah"]]
    assert jaw.max() > 0.95 and jaw.min() < 0.05
    assert np.array_equal(w[:, 16], w[:, 17]) and w[:, 16].max() == 1.0


def test_bad_shape_refused():
    with pytest.raises(ValueError):
        lip.encode(np.zeros((10, 16)))


def test_reads_the_headerless_and_short_size_kinds():
    # 36 vanilla files have no 12-byte header, just 0xFF then the packed data;
    # 47 have a size field 15 bytes short. Both must read like the rest.
    w = lip.test_pattern(1.0)
    packed = lip.encode(w, offset=-5)
    headerless = b"\xff" + packed[12:]
    back, offset = lip.decode(headerless)
    assert offset == -5 and np.array_equal(back, w)
    short = packed[:4] + struct.pack("<I", 24 + 132 * len(w) - 15) + packed[8:]
    back, _ = lip.decode(short)
    assert np.array_equal(back, w)
