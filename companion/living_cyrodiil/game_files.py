"""Finding the player's own Oblivion files (read only), for voices (Phase 3)
and later the vanilla dialogue and books.

The Data folder comes from `[game] data_folder` in settings.local.toml if set,
otherwise from the install path Oblivion records in the Windows registry.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_REGISTRY_KEYS = [
    r"SOFTWARE\WOW6432Node\Bethesda Softworks\Oblivion",
    r"SOFTWARE\Bethesda Softworks\Oblivion",
]

# The official voice archives, in load order (later ones win).
VOICE_ARCHIVES = [
    "Oblivion - Voices1.bsa",
    "Oblivion - Voices2.bsa",
    "Knights.bsa",
    "DLCShiveringIsles - Voices.bsa",
]


def _from_registry() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for key in _REGISTRY_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                installed, _ = winreg.QueryValueEx(k, "Installed Path")
                return Path(installed)
        except OSError:
            continue
    return None


def find_data_folder(configured: str = "") -> Path | None:
    """The game's Data folder, or None if it can't be found."""
    candidates: list[Path] = []
    if configured:
        p = Path(configured)
        candidates.append(p if p.name.lower() == "data" else p / "Data")
    # Started by the plugin (release): the running game's own Data folder.
    if os.environ.get("LC_GAME_DATA"):
        candidates.append(Path(os.environ["LC_GAME_DATA"]))
    reg = _from_registry()
    if reg:
        candidates.append(reg / "Data")
    for c in candidates:
        if (c / "Oblivion.esm").is_file():
            return c
    log.warning("Couldn't find Oblivion's Data folder (tried: %s). Set [game] data_folder in the companion's settings file.",
                ", ".join(str(c) for c in candidates) or "nothing configured, nothing in the registry")
    return None


def voice_archives(data: Path) -> list[Path]:
    return [data / name for name in VOICE_ARCHIVES if (data / name).is_file()]
