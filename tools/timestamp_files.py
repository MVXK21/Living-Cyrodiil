"""Independent proof that files existed at a given time (MVXK21, Nov 11: proof of
ownership for a takedown). Uses OpenTimestamps (https://opentimestamps.org),
free public calendars anchored in the Bitcoin blockchain. Only a SHA-256
fingerprint of each file leaves this PC, never the file.

The standard `ots` client doesn't run on Windows (python-bitcoinlib looks for
an old OpenSSL DLL), so this does the same with the core library.

Setup, once (its own environment, kept apart from the companion):
    py -3.14 -m venv build\\ots-venv
    build\\ots-venv\\Scripts\\python.exe -m pip install opentimestamps-client

Usage, from the project folder:
    build\\ots-venv\\Scripts\\python.exe tools\\timestamp_files.py stamp <file> ...
    build\\ots-venv\\Scripts\\python.exe tools\\timestamp_files.py upgrade <file.ots> ...
    build\\ots-venv\\Scripts\\python.exe tools\\timestamp_files.py info <file.ots>

`stamp` writes <file>.ots next to each file: a receipt that the calendars have
the fingerprint. A few hours later the calendars put it into a Bitcoin block;
`upgrade` then fetches that proof into the .ots, so the receipt no longer
depends on the calendars. Anyone can check a file and its .ots at
https://opentimestamps.org (drop both on the page).
"""

from __future__ import annotations

import os
import sys

from opentimestamps.calendar import RemoteCalendar
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import StreamDeserializationContext, StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

CALENDARS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
]


def save(dtf: DetachedTimestampFile, path: str) -> None:
    with open(path, "wb") as f:
        dtf.serialize(StreamSerializationContext(f))


def load(path: str) -> DetachedTimestampFile:
    with open(path, "rb") as f:
        return DetachedTimestampFile.deserialize(StreamDeserializationContext(f))


def stamp(path: str) -> bool:
    with open(path, "rb") as f:
        dtf = DetachedTimestampFile.from_fd(OpSHA256(), f)
    # A random nonce, as the ots client does, so the calendars learn nothing
    # about the file even from its fingerprint.
    tip = dtf.timestamp.ops.add(OpAppend(os.urandom(16))).ops.add(OpSHA256())
    ok = 0
    for url in CALENDARS:
        try:
            tip.merge(RemoteCalendar(url).submit(tip.msg, timeout=15))
            ok += 1
            print(f"  {url}: submitted")
        except Exception as e:              # one calendar being down is fine
            print(f"  {url}: failed ({e})")
    if not ok:
        print(f"{path}: no calendar took it; not stamped")
        return False
    save(dtf, path + ".ots")
    print(f"{path}: stamped by {ok} calendar(s) -> {path}.ots (sha256 {dtf.file_digest.hex()})")
    return True


def _walk(stamp):
    yield stamp
    for sub in stamp.ops.values():
        yield from _walk(sub)


def upgrade(path: str) -> None:
    dtf = load(path)
    changed = False
    # Each step that waits on a calendar: ask it for the finished proof.
    for sub in list(_walk(dtf.timestamp)):
        for attestation in list(sub.attestations):
            if not isinstance(attestation, PendingAttestation):
                continue
            try:
                found = RemoteCalendar(attestation.uri).get_timestamp(sub.msg, timeout=15)
            except Exception as e:
                print(f"  {attestation.uri}: not ready yet ({e.__class__.__name__})")
                continue
            sub.merge(found)
            changed = True
    if changed:
        save(dtf, path)
    info(path)


def info(path: str) -> None:
    dtf = load(path)
    print(f"{path}: file sha256 {dtf.file_digest.hex()}")
    for _msg, a in dtf.timestamp.all_attestations():
        if isinstance(a, BitcoinBlockHeaderAttestation):
            print(f"  confirmed in Bitcoin block {a.height}")
        elif isinstance(a, PendingAttestation):
            print(f"  pending at {a.uri} (run upgrade in a few hours)")


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("stamp", "upgrade", "info"):
        print(__doc__)
        return 1
    cmd, files = sys.argv[1], sys.argv[2:]
    for f in files:
        {"stamp": stamp, "upgrade": upgrade, "info": info}[cmd](f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
