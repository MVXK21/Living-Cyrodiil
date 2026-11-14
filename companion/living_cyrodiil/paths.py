"""Where the companion keeps its files.

Two layouts:

* **Development** (this repository): everything under the companion folder,
  as before.
* **Release** (installed from the mod zip, marked by a `release.txt` the
  packager writes next to the companion's code; alpha release, MVXK21, Nov 13,
  2026): the companion's code and data ship inside the mod, but everything it
  writes (settings, memory, logs, caches, the downloaded models, world
  knowledge) lives in `Documents\\My Games\\Oblivion\\LivingCyrodiil`, beside
  the plugin's log. So updating or reinstalling the mod never wipes a player's
  memories or the 1 GB of models, and nothing lands in MO2's overwrite folder.
  The API key file sits at the top of the mod folder (`API_KEY_FILE`), where
  MO2's "Open in Explorer" shows it.
"""

from __future__ import annotations

import os
from pathlib import Path

COMPANION_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = COMPANION_DIR / "data"
RELEASE = (COMPANION_DIR / "release.txt").exists()


def documents_dir() -> Path:
    """The user's Documents folder, wherever Windows keeps it (OneDrive
    redirects it, so it isn't always ~/Documents)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                            ("d4", ctypes.c_ubyte * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except (OSError, AttributeError, ValueError):
            pass
    return Path.home() / "Documents"


if RELEASE:
    # LC_USER_DIR: tests of a release build point it at a scratch folder, so
    # they never write into a real player folder.
    USER_DIR = (Path(os.environ["LC_USER_DIR"]) if os.environ.get("LC_USER_DIR")
                else documents_dir() / "My Games" / "Oblivion" / "LivingCyrodiil")
    MOD_DIR = COMPANION_DIR.parent              # the mod's own folder (Data, under MO2)
    API_KEY_FILE = MOD_DIR / "PASTE YOUR API KEY HERE.txt"
else:
    USER_DIR = COMPANION_DIR
    MOD_DIR = COMPANION_DIR
    API_KEY_FILE = None

LOG_DIR = USER_DIR / "logs"
CACHE_DIR = USER_DIR / "cache"
SETTINGS_FILE = USER_DIR / ("settings.toml" if RELEASE else "settings.local.toml")
SETTINGS_EXAMPLE = COMPANION_DIR / "settings.example.toml"
MODELS_DIR = USER_DIR / "models"
# Every downloaded model (speech and voice) goes in MODELS_DIR, whichever part
# loads first (with speech off, the voice model used to land in ~/.cache).
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
