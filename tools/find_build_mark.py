"""Finds the build marks in a copy of the test build, and which copy they
belong to (see make_tester_package.py --copies).

Usage (from the project folder):
    companion\\.venv\\Scripts\\python.exe tools\\find_build_mark.py <file, zip or folder> ...

It looks everywhere a mark is put (the plugin DLL, the companion's
build_mark.py and setting file, the package zip's comment), inside zips and
zips within zips too, and looks each code up in build/tester/copies-*.csv.
"""

from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = re.compile(rb"LCMARK:([0-9a-f]{24})")
UNMARKED = "0" * 24


def scan_bytes(data: bytes, where: str, found: list[tuple[str, str]]) -> None:
    for m in MARK.finditer(data):
        found.append((m.group(1).decode(), where))
    if data[:4] == b"PK\x03\x04":
        try:
            scan_zip(zipfile.ZipFile(io.BytesIO(data)), where, found)
        except zipfile.BadZipFile:
            pass


def scan_zip(z: zipfile.ZipFile, where: str, found: list[tuple[str, str]]) -> None:
    for m in MARK.finditer(z.comment or b""):
        found.append((m.group(1).decode(), f"{where} (zip comment)"))
    for info in z.infolist():
        if not info.is_dir():
            scan_bytes(z.read(info), f"{where} > {info.filename}", found)


def scan(path: Path, found: list[tuple[str, str]]) -> None:
    if path.is_dir():
        for f in sorted(path.rglob("*")):
            if f.is_file():
                scan_bytes(f.read_bytes(), str(f), found)
    elif path.is_file():
        scan_bytes(path.read_bytes(), str(path), found)
    else:
        print(f"Not found: {path}")


def ledger() -> dict[str, dict]:
    copies = {}
    for f in sorted((ROOT / "build" / "tester").glob("copies-*.csv")):
        with f.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                copies[row["build_mark"]] = row
    return copies


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    found: list[tuple[str, str]] = []
    for arg in sys.argv[1:]:
        scan(Path(arg), found)
    if not found:
        print("No build marks found (removed, or not a Living Cyrodiil build).")
        return 1
    copies = ledger()
    codes = sorted({c for c, _ in found})
    for code in codes:
        places = [w for c, w in found if c == code]
        if code == UNMARKED:
            who = "an unmarked build (not one of the tester copies)"
        elif code in copies:
            row = copies[code]
            who = f"copy {row['copy']} of {row['version']}, made {row['made']}" + \
                  (f", given to {row['given_to']}" if row.get("given_to") else " (given_to not filled in)")
        else:
            who = "not in the record (build/tester/copies-*.csv)"
        print(f"Build mark {code}: {who}")
        for w in places:
            print(f"    {w}")
    if len([c for c in codes if c != UNMARKED]) > 1:
        print("Warning: more than one mark; the copy may have been tampered with or mixed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
