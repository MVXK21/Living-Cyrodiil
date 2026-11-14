"""Makes a dated evidence folder for proof of authorship (MVXK21, Nov 11):
provenance/<date>/ with a snapshot of the source at the current commit and
manifest.txt, the SHA-256 fingerprints of that snapshot, the tester copies,
the build-mark records and the unmarked build. Then timestamp manifest.txt:
    build\\ots-venv\\Scripts\\python.exe tools\\timestamp_files.py stamp provenance\\<date>\\manifest.txt

Run from the project folder with any Python:
    companion\\.venv\\Scripts\\python.exe tools\\make_provenance.py
"""

from __future__ import annotations

import datetime
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTHOR = "MVXK21"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    if git("status", "--porcelain"):
        sys.exit("Commit everything first, so the snapshot matches a commit.")
    today = datetime.date.today().isoformat()
    folder = ROOT / "provenance" / today
    folder.mkdir(parents=True, exist_ok=True)
    commit = git("rev-parse", "HEAD")
    snapshot = folder / f"living-cyrodiil-source-{commit[:10]}.zip"
    git("archive", "--format=zip", f"--prefix=LivingCyrodiil/", "-o", str(snapshot), "HEAD")

    files = [snapshot]
    files += sorted((ROOT / "build" / "tester").glob("LivingCyrodiil-dev-*-copy*.zip"))
    files += sorted((ROOT / "build" / "tester").glob("copies-*.csv"))
    files += [p for p in sorted((ROOT / "build" / "package").rglob("*")) if p.is_file()]

    lines = [
        f"Living Cyrodiil: evidence of authorship, {today}",
        f"Author: {AUTHOR}. Copyright (c) 2026 {AUTHOR}. All rights reserved.",
        f"Git commit: {commit} ({git('log', '-1', '--format=%cI')}), {git('rev-list', '--count', 'HEAD')} commits since "
        f"{git('log', '--reverse', '--format=%cs').splitlines()[0]}",
        "",
        "SHA-256 fingerprints (sha256  size  file):",
    ]
    for f in files:
        lines.append(f"{sha256(f)}  {f.stat().st_size}  {f.relative_to(ROOT).as_posix()}")
    manifest = folder / "manifest.txt"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(manifest.read_text(encoding="utf-8"))
    print(f"Next: timestamp it: build\\ots-venv\\Scripts\\python.exe tools\\timestamp_files.py stamp {manifest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
