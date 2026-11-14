"""Builds the developer test package for outside testers (MVXK21, Nov 9-10).

Run after build.ps1, from the project folder:
    companion\\.venv\\Scripts\\python.exe tools\\make_tester_package.py              (one unmarked package)
    companion\\.venv\\Scripts\\python.exe tools\\make_tester_package.py --copies 4   (one marked copy per tester)

With --copies, each copy gets its own random build mark (see "build marks"
below), recorded in build/tester/copies-<version>.csv; tools/find_build_mark.py
tells which copy a leaked file came from.

Makes build/tester/LivingCyrodiil-dev-<plugin version>.zip with:
  LivingCyrodiil-mod-<version>.zip   the game mod (build/package)
  LivingCyrodiil Companion/          the companion, without anything of this PC's
                                     (no .venv, caches, models, memory, logs,
                                     settings.local.toml or world_knowledge.txt)
  README.md, INSTALL.md, USING.md, TESTING.md, REPORTING.md   (docs/tester)
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
import secrets
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "build" / "package"
COMPANION = ROOT / "companion"
OUT = ROOT / "build" / "tester"

# What of the companion goes to testers; everything else stays on this PC.
COMPANION_KEEP = ["living_cyrodiil", "data", "requirements.txt", "setup.ps1", "Start Companion.bat",
                  "companion.ps1", "chat.ps1", "settings.example.toml", "README.md"]
NEVER = {"__pycache__", ".pytest_cache", ".venv", "cache", "models", "memory", "logs", "recordings",
         "bench_results", "tts_test"}
NEVER_FILES = {"settings.local.toml", "world_knowledge.txt"}


def plugin_version() -> str:
    text = (ROOT / "plugin" / "src" / "version.h").read_text(encoding="utf-8")
    return re.search(r'LC_VERSION_STRING\s+"([^"]+)"', text).group(1)


def companion_version() -> str:
    text = (COMPANION / "living_cyrodiil" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def copy_companion(dest: Path) -> int:
    count = 0
    for name in COMPANION_KEEP:
        src = COMPANION / name
        if src.is_dir():
            for f in src.rglob("*"):
                rel = f.relative_to(COMPANION)
                if f.is_file() and not (set(rel.parts) & NEVER) and f.name not in NEVER_FILES and f.suffix != ".pyc":
                    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest / rel)
                    count += 1
        elif src.is_file():
            shutil.copy2(src, dest / name)
            count += 1
        else:
            sys.exit(f"Missing from the companion: {src}")
    return count


# ---- build marks (MVXK21, Nov 11: one traceable copy per tester) -------------------
# A random code per copy, in four places: the plugin DLL, the companion's
# build_mark.py, its setting file, and the package zip's comment. It says
# nothing about the tester or their PC. tools/find_build_mark.py reads it back.
PLACEHOLDER = "LCMARK:" + "0" * 24


def patch_bytes(data: bytes, code: str, where: str) -> bytes:
    old, new = PLACEHOLDER.encode("ascii"), ("LCMARK:" + code).encode("ascii")
    if data.count(old) != 1:
        sys.exit(f"Build mark placeholder found {data.count(old)} times in {where} (expected once); not packaging")
    return data.replace(old, new)


def patch_file(path: Path, code: str) -> None:
    path.write_bytes(patch_bytes(path.read_bytes(), code, str(path)))


def assemble(folder: Path, version: str, code: str | None) -> int:
    """One package folder; `code` marks it (None: an unmarked package)."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    mod_zip = folder / f"LivingCyrodiil-mod-{version}.zip"
    with zipfile.ZipFile(mod_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(PACKAGE.rglob("*")):
            if f.is_file():
                data = f.read_bytes()
                if code and f.name == "LivingCyrodiil.dll":
                    data = patch_bytes(data, code, "LivingCyrodiil.dll")
                z.writestr(f.relative_to(PACKAGE).as_posix(), data)

    files = copy_companion(folder / "LivingCyrodiil Companion")
    if code:
        patch_file(folder / "LivingCyrodiil Companion" / "living_cyrodiil" / "build_mark.py", code)
        patch_file(folder / "LivingCyrodiil Companion" / "data" / "setting_vanilla.toml", code)
    # Testers get the exact library versions tested here, not just minimums,
    # so a newer release can't break their install.
    frozen = subprocess.run([str(COMPANION / ".venv" / "Scripts" / "python.exe"), "-m", "pip", "freeze",
                             "--exclude-editable"], capture_output=True, text=True, check=True).stdout
    pins = [l for l in frozen.splitlines() if "==" in l and not l.startswith(("-e", "#"))]
    (folder / "LivingCyrodiil Companion" / "requirements.txt").write_text(
        "# Living Cyrodiil companion: the exact versions this test build was tested with.\n"
        + "\n".join(pins) + "\n", encoding="utf-8")
    for guide in (ROOT / "docs" / "tester").glob("*.md"):
        text = guide.read_text(encoding="utf-8").replace("<version>", version)
        (folder / guide.name).write_text(text, encoding="utf-8")
    notices = folder / "LivingCyrodiil Companion" / "third-party"
    shutil.copytree(ROOT / "docs" / "third-party", notices)
    # The copyright notice (MVXK21), at the top and with the companion.
    shutil.copy2(ROOT / "COPYRIGHT.md", folder / "COPYRIGHT.md")
    shutil.copy2(ROOT / "COPYRIGHT.md", folder / "LivingCyrodiil Companion" / "COPYRIGHT.md")

    # Nothing personal may go out.
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix in (".toml", ".txt", ".md", ".py", ".ps1", ".bat"):
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"sk-or-[A-Za-z0-9-]{10,}", text):
                sys.exit(f"An API key is in {f}; not packaging")
    for bad in ("settings.local.toml", "world_knowledge.txt"):
        if list(folder.rglob(bad)):
            sys.exit(f"{bad} got into the package; not packaging")

    final = folder.parent / f"{folder.name}.zip"
    if final.exists():
        final.unlink()
    with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as z:
        if code:
            z.comment = ("LCMARK:" + code).encode("ascii")
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, (Path(folder.name) / f.relative_to(folder)).as_posix())
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--copies", type=int, default=0,
                    help="make this many copies, each with its own build mark (0: one unmarked package)")
    args = ap.parse_args()
    if not (PACKAGE / "OBSE" / "Plugins" / "LivingCyrodiil.dll").is_file():
        sys.exit("Build the plugin first (build.ps1): build/package has no LivingCyrodiil.dll")
    version = plugin_version()
    name = f"LivingCyrodiil-dev-{version}"
    if not args.copies:
        files = assemble(OUT / name, version, None)
        print(f"Plugin {version}, companion {companion_version()}: {files} companion files")
        print(f"Package: {OUT / (name + '.zip')}")
        return 0

    ledger = OUT / f"copies-{version}.csv"
    new_ledger = not ledger.exists()
    rows = []
    for n in range(1, args.copies + 1):
        code = secrets.token_hex(12)                 # 24 random hex characters, nothing else
        folder = OUT / f"{name}-copy{n}"
        assemble(folder, version, code)
        shutil.rmtree(folder)                        # keep only the zip
        rows.append([str(n), code, version, datetime.date.today().isoformat(), f"{folder.name}.zip", ""])
        print(f"Copy {n}: {folder.name}.zip  build mark {code}")
    with ledger.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_ledger:
            w.writerow(["copy", "build_mark", "version", "made", "package", "given_to"])
        w.writerows(rows)
    print(f"Record of the marks: {ledger} (fill in given_to; keep it private, never ship it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
