"""Builds LivingCyrodiil.esp from scratch: no Construction Set, no hand edits.

The plugin holds the placeholder dialogue that lets an NPC "speak" an LLM
reply through the game's own dialogue system (Phase 2 step 6, see
docs/phase2-plan.md):

  LCPart    global (short). The plugin sets it to the part of the reply about
            to be said (1-4) just before each SayTo.
  LCQuest   quest, start-game-enabled, priority 50, no script. Owns the topic.
  LCReply   dialogue topic (type Conversation, like vanilla SayTo topics), so
            it never appears in the vanilla dialogue window.
  4 lines   one INFO per reply part, each with a single response and the
            condition GetGlobalValue LCPart == its part, so exactly one plays.
            Part p's text is "(Living Cyrodiil placeholder p)"; the plugin
            swaps it for chunk p. A long reply is said as parts one after
            another: outside the dialogue window the game says only the first
            response of a line (seen in game, Nov 3, 2026), so one line with
            several responses doesn't work.

Voice files (written by the plugin at run time, not shipped):
  Sound\\Voice\\LivingCyrodiil.esp\\<race>\\<M|F>\\LCQuest_LCReply_<line id>_1.mp3
where <line id> is the INFO's form id with the mod index as 00 (see LINES).

Record layouts match records read from Oblivion.esm itself (Nov 2-3, 2026):
groups in the order GLOB, DIAL, QUST; GLOB = EDID FNAM(1) FLTV(4);
INFO = DATA(3) QSTI then TRDT(16) NAM1 NAM2 per response, then CTDA(24) SCHR(20).

Form IDs are fixed (index 01 = this plugin, since it has one master), so
adding records later never renumbers existing ones.

Usage:  python tools/build_esp.py <output path>
Standard library only.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

# Fixed form IDs. 0x01 = "this plugin" (its master, Oblivion.esm, is 0x00).
FID_QUEST = 0x01000800
FID_TOPIC = 0x01000801
FID_GLOBAL_PART = 0x01000803
# Reply part -> INFO form id. The plugin's speak.h has the same table.
LINES = {1: 0x01000802, 2: 0x01000804, 3: 0x01000805, 4: 0x01000806}
# 0x807 and 0x808 were the LCHold global and hold package (plugin 0.7.0-0.7.3),
# removed when holding moved to SetRestrained. Never reuse them.
NEXT_OBJECT_ID = 0x809

QUEST_EDID = "LCQuest"
TOPIC_EDID = "LCReply"
GLOBAL_EDID = "LCPart"
PLACEHOLDER_PREFIX = "(Living Cyrodiil placeholder"     # + " <r>)"

QUEST_FLAG_START_GAME_ENABLED = 0x01
QUEST_PRIORITY = 50
# Topic type 1 = Conversation: what 130 of the 142 topics vanilla scripts use
# with SayTo are (Nov 3, 2026). Conversation topics never appear in the
# dialogue window's topic list.
DIAL_TYPE_CONVERSATION = 1
EMOTION_NEUTRAL = 0

# Condition layout, as in Oblivion.esm: compare op (high bits; 0x00 is ==),
# 3 unused, float value, function index, param 1, param 2, unused.
FUNC_GET_GLOBAL_VALUE = 74      # param 1 = the global's form id


def placeholder(response: int) -> str:
    return f"{PLACEHOLDER_PREFIX} {response})"


def zstr(text: str) -> bytes:
    """Null-terminated Windows-1252 string, as Oblivion stores text."""
    return text.encode("cp1252") + b"\0"


def sub(tag: str, payload: bytes) -> bytes:
    assert len(tag) == 4 and len(payload) < 0x10000, tag
    return tag.encode("ascii") + struct.pack("<H", len(payload)) + payload


def record(tag: str, form_id: int, subrecords: list[bytes], flags: int = 0) -> bytes:
    body = b"".join(subrecords)
    return tag.encode("ascii") + struct.pack("<IIII", len(body), flags, form_id, 0) + body


def group(label: bytes, group_type: int, contents: list[bytes]) -> bytes:
    body = b"".join(contents)
    return b"GRUP" + struct.pack("<I", 20 + len(body)) + label + struct.pack("<iI", group_type, 0) + body


def condition_equals_global(value: int) -> bytes:
    return struct.pack("<B3sfIIII", 0x00, b"\0" * 3, float(value), FUNC_GET_GLOBAL_VALUE,
                       FID_GLOBAL_PART, 0, 0)


def info_line(form_id: int, part: int) -> bytes:
    return record("INFO", form_id, [
        sub("DATA", struct.pack("<BBB", DIAL_TYPE_CONVERSATION, 0, 0)),   # type, next speaker, flags
        sub("QSTI", struct.pack("<I", FID_QUEST)),
        # emotion type, emotion value, unused, response number (1), unused
        sub("TRDT", struct.pack("<Ii4sB3s", EMOTION_NEUTRAL, 50, b"\0" * 4, 1, b"\0" * 3)),
        sub("NAM1", zstr(placeholder(part))),
        sub("NAM2", zstr("")),
        sub("CTDA", condition_equals_global(part)),
        sub("SCHR", b"\0" * 20),        # empty result script, as every vanilla INFO has
    ])


def build() -> bytes:
    part_global = record("GLOB", FID_GLOBAL_PART, [
        sub("EDID", zstr(GLOBAL_EDID)),
        sub("FNAM", b"s"),                      # short
        sub("FLTV", struct.pack("<f", 1.0)),
    ])

    quest = record("QUST", FID_QUEST, [
        sub("EDID", zstr(QUEST_EDID)),
        sub("FULL", zstr("Living Cyrodiil")),
        sub("DATA", struct.pack("<BB", QUEST_FLAG_START_GAME_ENABLED, QUEST_PRIORITY)),   # flags, priority
    ])

    topic = record("DIAL", FID_TOPIC, [
        sub("EDID", zstr(TOPIC_EDID)),
        sub("QSTI", struct.pack("<I", FID_QUEST)),
        sub("FULL", zstr(TOPIC_EDID)),
        sub("DATA", struct.pack("<B", DIAL_TYPE_CONVERSATION)),
    ])
    infos = [info_line(fid, part) for part, fid in sorted(LINES.items())]

    glob_group = group(b"GLOB", 0, [part_global])
    dial_group = group(b"DIAL", 0, [
        topic,
        group(struct.pack("<I", FID_TOPIC), 7, infos),     # type 7 = topic children
    ])
    quest_group = group(b"QUST", 0, [quest])

    num_records = 1 + 1 + 1 + len(infos) + 4    # GLOB, QUST, DIAL, INFOs + four groups
    header = record("TES4", 0, [
        sub("HEDR", struct.pack("<fiI", 1.0, num_records, NEXT_OBJECT_ID)),
        sub("CNAM", zstr("Living Cyrodiil")),
        sub("SNAM", zstr("Placeholder dialogue for Living Cyrodiil. Built by tools/build_esp.py.")),
        sub("MAST", zstr("Oblivion.esm")),
        sub("DATA", struct.pack("<Q", 0)),
    ])

    # Same group order as Oblivion.esm: GLOB, then DIAL before QUST.
    return header + glob_group + dial_group + quest_group


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = Path(argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
