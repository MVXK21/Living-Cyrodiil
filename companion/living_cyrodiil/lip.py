"""Oblivion .lip files (Phase 4): the game's own lip-sync, one file next to
each voice file, so NPC mouths move with our speech like vanilla dialogue.

Format (decoded Nov 7 from the player's own archives; docs/lipsync-research.md):

    header, 12 bytes:  uint32 1, uint32 unpacked size (24 + 132 x frames), uint32 1
    then zero run-length encoding: a 00 byte and a uint16 count stand for
    that many zero bytes; any other byte is itself. Unpacked:
        uint32 frames (30 a second), int32 offset (lip frame t goes with
        audio frame t + offset; we use 0), then frames x 33 float32 weights.

The 33 weights: 16 mouth shapes (CHANNELS), blinks, brows, eye direction and
a slight head movement.
"""

from __future__ import annotations

import struct

import numpy as np

FPS = 30
WEIGHTS = 33
MOUTH_SHAPES = ("Aah", "BigAah", "BMP", "ChJSh", "DST", "Eee", "Eh", "FV",
                "I", "K", "N", "Oh", "OohQ", "R", "Th", "W")
CHANNELS = {name: i for i, name in enumerate(MOUTH_SHAPES)}
CHANNELS.update({"BlinkLeft": 16, "BlinkRight": 17,
                 "BrowDownLeft": 18, "BrowDownRight": 19, "BrowInLeft": 20, "BrowInRight": 21,
                 "BrowUpLeft": 22, "BrowUpRight": 23,
                 "LookDown": 24, "LookLeft": 25, "LookRight": 26, "LookUp": 27,
                 "SquintLeft": 28, "SquintRight": 29})


def frames_for(seconds: float) -> int:
    return max(1, int(round(seconds * FPS)))


def encode(weights: np.ndarray, offset: int = 0) -> bytes:
    """A .lip file's bytes for `weights` (frames x 33, 30 frames a second),
    packed exactly as the game's own files are."""
    w = np.ascontiguousarray(weights, dtype="<f4")
    if w.ndim != 2 or w.shape[1] != WEIGHTS or w.shape[0] == 0:
        raise ValueError(f"lip weights must be frames x {WEIGHTS}, not {w.shape}")
    n = w.shape[0]
    raw = struct.pack("<Ii", n, offset) + w.tobytes()
    raw = raw.rstrip(b"\0")                    # the game's files never store the trailing zeros
    out = bytearray(struct.pack("<3I", 1, 24 + 132 * n, 1))
    i = 0
    while i < len(raw):
        if raw[i] == 0:
            j = i
            while j < len(raw) and raw[j] == 0 and j - i < 0xFFFF:
                j += 1
            out += b"\0" + struct.pack("<H", j - i)
            i = j
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def decode(data: bytes) -> tuple[np.ndarray, int]:
    """(weights frames x 33, offset) from a .lip file's bytes.

    Of the 50,698 vanilla files (counted Nov 7, 2026), 50,615 follow the
    format exactly; 47 have a size field 15 bytes short (the data is
    complete, and the size isn't needed); 36 have no 12-byte header, just a
    0xFF byte before a complete packed stream. All three read correctly."""
    version, size, packed = struct.unpack_from("<3I", data, 0)
    start = 12
    if version != 1:
        if data[:1] != b"\xff":
            raise ValueError(f"not a .lip file this code understands (version {version})")
        packed, start = 1, 1                       # the headerless kind
    if packed:
        raw = bytearray()
        i = start
        while i < len(data):
            if data[i] == 0:
                raw += bytes(data[i + 1] | (data[i + 2] << 8))
                i += 3
            else:
                raw.append(data[i])
                i += 1
    else:
        raw = bytearray(data[12:])
    n, offset = struct.unpack_from("<Ii", raw, 0)
    body = bytes(raw[8:]).ljust(132 * n, b"\0")[:132 * n]
    return np.frombuffer(body, "<f4").reshape(n, WEIGHTS).copy(), offset


def test_pattern(seconds: float) -> np.ndarray:
    """Phase 4 spike: a pattern nobody could miss. The jaw opens and closes
    three times a second (BigAah) all through the line, and both eyes blink
    once a second. If the game plays our file, the NPC looks like this."""
    n = frames_for(seconds)
    t = np.arange(n) / FPS
    w = np.zeros((n, WEIGHTS), np.float32)
    w[:, CHANNELS["BigAah"]] = 0.5 - 0.5 * np.cos(2 * np.pi * 3 * t)
    blink = ((t % 1.0) < 0.12).astype(np.float32)
    w[:, CHANNELS["BlinkLeft"]] = blink
    w[:, CHANNELS["BlinkRight"]] = blink
    return w
