"""Read-only access to Oblivion's .bsa archives (version 103), for the player's
own voice files (Phase 3). Nothing read here is ever shipped with the mod.

    archive = BsaArchive(Path(".../Oblivion - Voices1.bsa"))
    for path in archive.files("sound\\voice\\oblivion.esm\\argonian\\f"):
        data = archive.read(path)
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

_COMPRESSED_BY_DEFAULT = 0x4
_SIZE_FLIP_COMPRESSION = 0x40000000


@dataclass(frozen=True)
class _Entry:
    size: int
    offset: int
    compressed: bool


class BsaError(Exception):
    pass


class BsaArchive:
    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, _Entry] = {}
        with open(path, "rb") as f:
            header = f.read(36)
            if len(header) < 36 or header[:4] != b"BSA\0":
                raise BsaError(f"{path.name} isn't a BSA archive")
            (_magic, version, _folder_offset, flags, folder_count, file_count,
             _folder_names_len, file_names_len, _file_flags) = struct.unpack("<4sIIIIIIII", header)
            if version != 103:
                raise BsaError(f"{path.name} is BSA version {version}; only Oblivion's 103 is supported")
            compressed_default = bool(flags & _COMPRESSED_BY_DEFAULT)
            folders = [struct.unpack("<QII", f.read(16)) for _ in range(folder_count)]
            records: list[tuple[str, int, int, bool]] = []
            for _hash, count, _offset in folders:
                name_len = f.read(1)[0]
                folder = f.read(name_len).rstrip(b"\0").decode("latin-1").lower()
                for _ in range(count):
                    _fhash, size, offset = struct.unpack("<QII", f.read(16))
                    flip = bool(size & _SIZE_FLIP_COMPRESSION)
                    records.append((folder, size & 0x3FFFFFFF, offset, compressed_default != flip))
            names = f.read(file_names_len).split(b"\0")
        for (folder, size, offset, compressed), name in zip(records, names):
            self._entries[f"{folder}\\{name.decode('latin-1').lower()}"] = _Entry(size, offset, compressed)

    def files(self, folder: str = "") -> list[str]:
        """File paths (lower case, backslashes), optionally only those in `folder`."""
        prefix = folder.lower().strip("\\")
        if not prefix:
            return sorted(self._entries)
        prefix += "\\"
        return sorted(p for p in self._entries if p.startswith(prefix))

    def folders(self) -> list[str]:
        return sorted({p.rsplit("\\", 1)[0] for p in self._entries})

    def read(self, path: str) -> bytes:
        entry = self._entries.get(path.lower())
        if entry is None:
            raise KeyError(path)
        with open(self.path, "rb") as f:
            f.seek(entry.offset)
            data = f.read(entry.size)
        if entry.compressed:
            data = zlib.decompress(data[4:])          # first 4 bytes: original size
        return data
