"""Builds the public alpha release: one zip that installs like any mod (MVXK21,
Nov 13, 2026: "Install, open in explorer, type in api key, refresh, play").

Run after build.ps1, from the project folder:
    companion\\.venv\\Scripts\\python.exe tools\\make_release.py

Makes build/release/LivingCyrodiil-<plugin version>-alpha.zip, whose top level
is the mod folder:
  LivingCyrodiil.esp, OBSE/Plugins/LivingCyrodiil.dll and .ini   (build/package)
  PASTE YOUR API KEY HERE.txt                                     (the player pastes their key)
  Collect logs.bat                                                (zips both logs for a bug report)
  Living Cyrodiil - README.md, COPYRIGHT.md
  LCCompanion/            the companion: code, data, release.txt, and its own
                          Python (python/) with every library, so players
                          install nothing. The plugin starts it with the game.

Nothing of this PC goes in: no .venv, caches, models, memory, logs, settings or
world knowledge, and no API key (checked). The Python is this PC's 3.14 base
install plus the companion's libraries, minus what the companion never uses.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_tester_package as tester  # noqa: E402

ROOT = tester.ROOT
COMPANION = tester.COMPANION
PACKAGE = tester.PACKAGE
OUT = ROOT / "build" / "release"
VENV = COMPANION / ".venv"

COMPANION_KEEP = ["living_cyrodiil", "data", "settings.example.toml", "requirements.txt"]
# The base Python: what the companion never uses stays out.
STDLIB_SKIP = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__", "venv"}
BASE_FILES = ["python.exe", "pythonw.exe", "python3.dll", "python314.dll", "vcruntime140.dll",
              "vcruntime140_1.dll", "LICENSE.txt"]
# The companion's libraries: build-only files stay out.
LIBS_SKIP_DIRS = {"__pycache__", "pip", "include"}
LIBS_SKIP_SUFFIXES = {".pyc", ".lib", ".pdb", ".h", ".hpp", ".cuh"}
LIBS_SKIP_PREFIXES = ("pip-",)


def base_python() -> Path:
    cfg = (VENV / "pyvenv.cfg").read_text(encoding="utf-8")
    home = re.search(r"(?m)^home\s*=\s*(.+)$", cfg).group(1).strip()
    return Path(home)


def copy_tree(src: Path, dest: Path, skip_dirs: set[str], skip_suffixes=frozenset(), skip_prefixes=()) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel_dir = Path(dirpath).relative_to(src)
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(skip_prefixes)]
        for name in filenames:
            if Path(name).suffix.lower() in skip_suffixes:
                continue
            target = dest / rel_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(dirpath) / name, target)
            count += 1
    return count


def vc_runtime() -> Path:
    """Visual Studio's redistributable C++ runtime (x64), newest first. PyTorch,
    CTranslate2 and ONNX Runtime need MSVCP140*.dll and VCRUNTIME140_THREADS.dll,
    which a PC without the Visual C++ Redistributable lacks (Nov 14 release
    check). Microsoft allows shipping these beside the program (app-local)."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    vs = subprocess.run([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                        capture_output=True, text=True, check=True).stdout.strip()
    found = sorted((Path(vs) / "VC" / "Redist" / "MSVC").glob("*/x64/Microsoft.VC*.CRT"),
                   key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parent.parent.name.split(".")], reverse=True)
    for crt in found:
        if (crt / "vcruntime140_threads.dll").exists():
            return crt
    sys.exit("No Visual C++ runtime with vcruntime140_threads.dll in Visual Studio's Redist folder; not packaging")


def bundle_python(dest: Path) -> int:
    base = base_python()
    dest.mkdir(parents=True)
    for name in BASE_FILES:
        shutil.copy2(base / name, dest / name)
    count = len(BASE_FILES)
    # The C++ runtime beside python.exe, where Windows looks first for every DLL
    # the libraries load (newer than Python's own vcruntime140, and compatible).
    crt = vc_runtime()
    for dll in crt.glob("*.dll"):
        if dll.name.lower() != "vccorlib140.dll":       # C++/CX only, not needed
            shutil.copy2(dll, dest / dll.name)
            count += 1
    print(f"  C++ runtime from {crt}")
    count += copy_tree(base / "DLLs", dest / "DLLs", {"__pycache__"})
    count += copy_tree(base / "Lib", dest / "Lib", STDLIB_SKIP)
    count += copy_tree(VENV / "Lib" / "site-packages", dest / "Lib" / "site-packages",
                       LIBS_SKIP_DIRS, LIBS_SKIP_SUFFIXES, LIBS_SKIP_PREFIXES)
    # torch ships its build files (CMake, headers) next to the library.
    for extra in ("torch/share", "torch/include", "torch/utils/benchmark/examples"):
        shutil.rmtree(dest / "Lib" / "site-packages" / extra, ignore_errors=True)
    return count


COLLECT_LOGS = r"""@echo off
rem Living Cyrodiil: zips both logs onto your desktop, ready to attach to a bug report.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$docs = [Environment]::GetFolderPath('MyDocuments');" ^
  "$src = Join-Path $docs 'My Games\Oblivion\LivingCyrodiil';" ^
  "$out = Join-Path ([Environment]::GetFolderPath('Desktop')) 'LivingCyrodiil-logs.zip';" ^
  "$files = @(Get-ChildItem -Path $src -Filter 'LivingCyrodiil*.log' -ErrorAction SilentlyContinue) + @(Get-ChildItem -Path (Join-Path $src 'logs') -Filter 'companion.log*' -ErrorAction SilentlyContinue);" ^
  "if ($files.Count -eq 0) { Write-Host ('No logs found in ' + $src + '. Play once first.') } else { Compress-Archive -Path $files.FullName -DestinationPath $out -Force; Write-Host ('Saved ' + $out) }"
pause
"""


def assemble(folder: Path, version: str) -> None:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    # The game side.
    for f in PACKAGE.rglob("*"):
        if f.is_file() and f.suffix != ".pdb":
            target = folder / f.relative_to(PACKAGE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    # The companion: code and data, then its Python.
    comp = folder / "LCCompanion"
    comp.mkdir()
    for name in COMPANION_KEEP:
        src = COMPANION / name
        if src.is_dir():
            copy_tree(src, comp / name, tester.NEVER, {".pyc"})
        else:
            shutil.copy2(src, comp / name)
    shutil.copytree(ROOT / "docs" / "third-party", comp / "third-party")
    shutil.copy2(ROOT / "COPYRIGHT.md", comp / "COPYRIGHT.md")
    (comp / "release.txt").write_text(
        f"Living Cyrodiil {version} (alpha). This file tells the companion it was installed from the mod zip:\n"
        "your settings, memories, logs and downloaded models live in Documents\\My Games\\Oblivion\\LivingCyrodiil.\n",
        encoding="utf-8")
    print("Bundling Python and the companion's libraries (a few minutes)...")
    n = bundle_python(comp / "python")
    print(f"  {n} files")
    # The player's side.
    sys.path.insert(0, str(COMPANION))
    from living_cyrodiil.settings import KEY_FILE_TEMPLATE
    (folder / "PASTE YOUR API KEY HERE.txt").write_text(KEY_FILE_TEMPLATE, encoding="utf-8")
    (folder / "Collect logs.bat").write_text(COLLECT_LOGS.replace("\n", "\r\n"), encoding="utf-8")
    readme = (ROOT / "docs" / "release" / "README.md").read_text(encoding="utf-8").replace("<version>", version)
    (folder / "Living Cyrodiil - README.md").write_text(readme, encoding="utf-8")
    shutil.copy2(ROOT / "COPYRIGHT.md", folder / "COPYRIGHT.md")
    check(folder)


def check(folder: Path) -> None:
    """Nothing personal may go out."""
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix in (".toml", ".txt", ".md", ".py", ".ps1", ".bat", ".ini", ".json"):
            if "python" in f.relative_to(folder).parts[:2]:
                continue          # the libraries' own files
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"sk-or-[A-Za-z0-9-]{10,}", text):
                sys.exit(f"An API key is in {f}; not packaging")
    for bad in ("settings.local.toml", "world_knowledge.txt", "copies-", "sending-tester-copies"):
        if [p for p in folder.rglob("*") if bad in p.name]:
            sys.exit(f"{bad} got into the release; not packaging")
    for bad_dir in ("memory", "logs", "cache", "models", ".venv"):
        if [p for p in (folder / "LCCompanion").iterdir() if p.name == bad_dir]:
            sys.exit(f"{bad_dir} got into the release; not packaging")
    longest = max((len(p.relative_to(folder / "LCCompanion").as_posix()) for p in (folder / "LCCompanion").rglob("*")), default=0)
    print(f"  longest path inside LCCompanion: {longest} characters")


def main() -> int:
    version = tester.plugin_version()
    if not (PACKAGE / "OBSE" / "Plugins" / "LivingCyrodiil.dll").exists():
        sys.exit("Run build.ps1 first.")
    folder = OUT / f"LivingCyrodiil-{version}-alpha"
    assemble(folder, version)
    final = OUT / f"{folder.name}.zip"
    if final.exists():
        final.unlink()
    print("Zipping...")
    with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(folder).as_posix())     # the zip's top level is the mod folder
    print(f"Release: {final} ({final.stat().st_size / 1e6:.0f} MB), plugin {version}, companion {tester.companion_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
