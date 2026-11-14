"""NPC voices for Pocket TTS (Phase 3 step 2), built from the player's own
installed voice files. Nothing here is shipped with the mod.

A *voice type* is a voice folder in the game's archives, such as
`oblivion.esm\\nord\\m`. Its reference clip is a handful of the game's own
lines joined together; Pocket TTS clones the voice from it.

Choosing the lines matters (MVXK21, Nov 5, 2026: a Nord clone from two random
lines sounded "a little off"):
* Only **generic topics** (InfoGeneral, Greeting, Hello, Goodbye). Oblivion
  records those once per race and sex, so they're all the same actor; quest
  lines and named characters' lines can be other actors.
* Calm, clean lines: 2.5-8 seconds, not clipped, normal loudness (no shouting
  or whispering). Volumes are evened out before joining.
* About 15 seconds in all.

Which voice an NPC gets (`VoiceLibrary.choose`), the first that works:
1. Their **own voice**, if Oblivion.esm has enough clean lines written just
   for them (Baurus, Jauffre, Witseidutsei...).
2. Their **voice race** and sex: the race whose recordings their race borrows
   (in vanilla, Dark and Wood Elves use High Elf voices, Khajiit Argonian,
   Orcs Nord, and Breton women Imperial). The plugin reads it live, so modded
   races work too.
3. Their own race and sex.
4. Imperial of their sex, then Imperial male.
Oblivion.esm's voice folders come first; another plugin's folder (a modded
race's own recordings) is used only when Oblivion.esm has none for that race.

Reference clips (WAV) and Pocket TTS's voice states (`VoiceBank`) are cached
in companion\\cache\\voices\\, so a voice is only built once; after that it
loads from its saved state.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio, esm
from .bsa import BsaArchive
from .game_files import voice_archives
from .paths import CACHE_DIR as USER_CACHE

log = logging.getLogger(__name__)

CACHE_DIR = USER_CACHE / "voices"
GENERIC_TOPICS = ("infogeneral", "greeting", "hello", "goodbye")
MIN_LINE, MAX_LINE = 2.5, 8.0          # seconds
TARGET_SECONDS = 15.0
TARGET_RMS = 0.08                       # loudness every line is evened out to
GAP_SECONDS = 0.25
REFERENCE_RATE = 24000                  # Pocket TTS's own sample rate


@dataclass(frozen=True)
class VoiceType:
    plugin: str      # "oblivion.esm"
    race: str        # "nord"
    sex: str         # "m" or "f"

    @property
    def folder(self) -> str:
        return f"sound\\voice\\{self.plugin}\\{self.race}\\{self.sex}".lower()

    @property
    def key(self) -> str:
        return f"{self.plugin}_{self.race}_{self.sex}".lower().replace(" ", "-")

    @staticmethod
    def for_npc(race: str, female: bool, plugin: str = "oblivion.esm") -> "VoiceType":
        return VoiceType(plugin.lower(), race.strip().lower(), "f" if female else "m")


def _topic(file_name: str) -> str:
    """'generic_infogeneral_0001a2b3_1.mp3' -> 'infogeneral'."""
    parts = file_name.split("_")
    return parts[-3] if len(parts) >= 4 else ""


def _rms(clip: np.ndarray) -> float:
    return float(np.sqrt(np.mean(clip ** 2))) if clip.size else 0.0


class VoiceLibrary:
    """Finds the game's voice lines and builds reference clips from them."""

    def __init__(self, data_folder: Path, sample_rate: int, cache_dir: Path = CACHE_DIR):
        self.sample_rate = sample_rate
        self.cache_dir = cache_dir
        self.archives = [BsaArchive(p) for p in voice_archives(data_folder)]
        # Lines written for one named NPC (their own actor): kept out of the
        # voice-type references, and used for that NPC's own voice instead.
        esm_path = data_folder / "Oblivion.esm"
        self.speakers = esm.speakers(esm_path) if esm_path.is_file() else {}
        self._folders: dict[str, list[tuple[BsaArchive, str]]] = {}
        self._npc_files: dict[str, list[tuple[BsaArchive, str]]] = {}   # NPC base id -> their lines
        for a in self.archives:
            for path in a.files():
                if path.endswith(".mp3") and path.startswith("sound\\voice\\"):
                    self._folders.setdefault(path.rsplit("\\", 1)[0], []).append((a, path))
                    speaker = self.speakers.get(esm.info_id(path))
                    if speaker:
                        self._npc_files.setdefault(speaker, []).append((a, path))
        log.info("Voice library: %d voice folders in %d archives", len(self._folders), len(self.archives))

    def voice_types(self) -> list[VoiceType]:
        out = []
        for folder in sorted(self._folders):
            parts = folder.split("\\")
            if len(parts) == 5:
                out.append(VoiceType(parts[2], parts[3], parts[4]))
        return out

    def has(self, voice: VoiceType) -> bool:
        return voice.folder in self._folders

    def lines(self, voice: VoiceType, generic_only: bool = True) -> list[tuple[BsaArchive, str]]:
        found = self._folders.get(voice.folder, [])
        if generic_only:
            found = [(a, p) for a, p in found
                     if _topic(p.rsplit("\\", 1)[1]) in GENERIC_TOPICS
                     and esm.info_id(p) not in self.speakers]
        return found

    def build_reference(self, voice: VoiceType, seconds: float = TARGET_SECONDS) -> tuple[np.ndarray, list[str]]:
        """The reference clip for a voice type, and the lines it's made of."""
        candidates = self.lines(voice)
        if not candidates:
            raise LookupError(f"No generic voice lines for {voice.folder}")
        built = self._build(candidates, voice.folder, voice.key, seconds, minimum=0.1)
        if built is None:
            raise LookupError(f"No clean generic lines for {voice.folder}")
        return built

    def _build(self, candidates, label: str, seed: str, seconds: float,
               minimum: float) -> tuple[np.ndarray, list[str]] | None:
        candidates = list(candidates)
        random.Random(seed).shuffle(candidates)            # the same picks every time
        clips: list[np.ndarray] = []
        used: list[str] = []
        total = 0.0
        gap = np.zeros(int(GAP_SECONDS * self.sample_rate), np.float32)
        for archive, path in candidates[:400]:
            clip = audio.trim_silence(audio.decode(archive.read(path), self.sample_rate), self.sample_rate)
            length = audio.duration(clip, self.sample_rate)
            rms = _rms(clip)
            if not MIN_LINE <= length <= MAX_LINE or np.abs(clip).max() > 0.97 or not 0.02 <= rms <= 0.3:
                continue
            clips += [clip * (TARGET_RMS / rms), gap]
            used.append(path.rsplit("\\", 1)[1])
            total += length
            if total >= seconds:
                break
        if not clips or total < minimum:
            return None
        joined = np.clip(np.concatenate(clips), -1.0, 1.0).astype(np.float32)
        log.info("Reference for %s: %d lines, %.1fs", label, len(used), total)
        return joined, used

    # Combat and pain sounds make poor references.
    NOISY_TOPICS = {"attack", "powerattack", "hit", "flee", "yield", "death", "assault", "murder",
                    "trespass", "steal", "pickpocket", "acceptyield", "noticecorpse", "taunt"}

    def npc_lines(self, npc_base_id: str, race: str, sex: str) -> list[tuple[BsaArchive, str]]:
        """The game lines written for this one NPC, from the one voice folder
        the game plays for them (`race`: their voice race, or their own race;
        `sex`: "m" or "f").

        The game records a named NPC's lines for several races (Hannibal
        Traven's are under both Breton and Imperial male, read differently),
        so only the folder the player actually hears is their voice (MVXK21,
        Nov 5, 2026: a clone from both folders sounded like neither)."""
        folder = f"\\{race.strip().lower()}\\{sex}"
        return [(a, p) for a, p in self._npc_files.get(npc_base_id.upper(), [])
                if p.rsplit("\\", 1)[0].endswith(folder)
                and _topic(p.rsplit("\\", 1)[1]) not in self.NOISY_TOPICS]

    def build_npc_reference(self, npc_base_id: str, race: str, sex: str, seconds: float = TARGET_SECONDS,
                            minimum: float = 6.0) -> tuple[np.ndarray, list[str]] | None:
        """A reference clip from the NPC's own lines, or None if they have too
        few clean ones (then their voice type is used)."""
        return self._build(self.npc_lines(npc_base_id, race, sex), f"npc {npc_base_id} ({race} {sex})",
                           npc_base_id, seconds, minimum)

    @staticmethod
    def npc_key(npc_base_id: str, race: str, sex: str) -> str:
        return f"npc_{npc_base_id.upper()}_{race.strip().lower().replace(' ', '-')}_{sex}"

    def npc_reference_path(self, npc_base_id: str, race: str, sex: str) -> Path | None:
        if not npc_base_id or npc_base_id.upper() not in self._npc_files or not race.strip():
            return None                       # no lines of their own at all
        key = self.npc_key(npc_base_id, race, sex)
        path = self.cache_dir / f"{key}.wav"
        missing = self.cache_dir / f"{key}.none"
        if path.exists():
            return path
        if missing.exists():
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        built = self.build_npc_reference(npc_base_id, race, sex)
        if built is None:
            missing.write_text("too few clean lines of their own\n", encoding="utf-8")
            return None
        clip, used = built
        audio.write_wav(path, clip, self.sample_rate)
        (self.cache_dir / f"{key}.txt").write_text("\n".join(used) + "\n", encoding="utf-8")
        return path

    def reference_path(self, voice: VoiceType) -> Path:
        """The cached reference WAV for a voice type, built if needed."""
        path = self.cache_dir / f"{voice.key}.wav"
        if not path.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            clip, used = self.build_reference(voice)
            audio.write_wav(path, clip, self.sample_rate)
            (self.cache_dir / f"{voice.key}.txt").write_text("\n".join(used) + "\n", encoding="utf-8")
        return path

    # -- choosing a voice for an NPC ------------------------------------------

    def _types_for(self, race: str, sex: str) -> list[VoiceType]:
        """Voice folders for this race and sex: Oblivion.esm's first, then any
        other plugin's."""
        race = race.strip().lower()
        found = [v for v in self.voice_types() if v.race == race and v.sex == sex]
        return sorted(found, key=lambda v: v.plugin != "oblivion.esm")

    def choose(self, *, base_id: str = "", race: str = "", voice_race: str = "",
               female: bool = False, name: str = "") -> "VoiceChoice":
        """The voice this NPC speaks with (see the module notes for the order).
        Builds and caches the reference clip the first time."""
        who = name or base_id or "NPC"
        sex = "f" if female else "m"
        heard = voice_race if voice_race.strip() else race    # the folder the game plays
        own = self.npc_reference_path(base_id, heard, sex)
        if own is not None:
            return VoiceChoice(self.npc_key(base_id, heard, sex), f"{who}'s own voice", own, own_voice=True)
        wanted = [r.strip().lower() for r in (voice_race, race) if r.strip()]
        tried: set[str] = set()
        for r, s in [(r, sex) for r in wanted] + [("imperial", sex), ("imperial", "m")]:
            for voice in self._types_for(r, s):
                if voice.key in tried:
                    continue
                tried.add(voice.key)
                try:
                    ref = self.reference_path(voice)
                except LookupError as e:
                    log.info("Voice %s unusable for %s: %s", voice.key, who, e)
                    continue
                fallback = r not in wanted or s != sex
                label = f"{voice.race.title()} {'female' if voice.sex == 'f' else 'male'}"
                if voice.plugin != "oblivion.esm":
                    label += f" ({voice.plugin})"
                if fallback:
                    label += " (fallback)"
                    log.warning("No voice for %s (race %r, voice race %r, %s); using %s",
                                who, race, voice_race, "female" if female else "male", voice.key)
                return VoiceChoice(voice.key, label, ref, fallback=fallback)
        raise LookupError(f"No usable voice at all for {who}; are the game's voice archives missing?")


@dataclass(frozen=True)
class VoiceChoice:
    key: str                 # cache name: "oblivion.esm_nord_m" or "npc_00023F2A"
    label: str               # for the companion window and log: "Nord male", "Baurus's own voice"
    reference: Path          # the reference WAV
    own_voice: bool = False  # cloned from lines written for this NPC
    fallback: bool = False   # neither their race nor their voice race had a usable voice


class VoiceBank:
    """Pocket TTS voice states: built once from a reference WAV, saved as
    .safetensors, and reloaded from there afterwards.

    States are kept per model (`model_tag`, e.g. the config file's name),
    since a state only fits the model that made it. A state older than its
    reference WAV is rebuilt. Safe to use from several threads."""

    def __init__(self, model, model_tag: str = "default", cache_dir: Path = CACHE_DIR):
        self.model = model
        self.state_dir = cache_dir / "states" / model_tag
        self._states: dict[str, dict] = {}
        # Pocket TTS isn't thread-safe: one lock for building and speaking.
        self._lock = threading.RLock()

    def state_path(self, choice: VoiceChoice) -> Path:
        return self.state_dir / f"{choice.key}.safetensors"

    def state(self, choice: VoiceChoice) -> dict:
        with self._lock:
            if choice.key in self._states:
                return self._states[choice.key]
            path = self.state_path(choice)
            t = time.perf_counter()
            if path.exists() and path.stat().st_mtime >= choice.reference.stat().st_mtime:
                state = self.model.get_state_for_audio_prompt(path)
                log.info("Voice %s loaded from its saved state in %.2fs", choice.key, time.perf_counter() - t)
            else:
                from pocket_tts import export_model_state
                state = self.model.get_state_for_audio_prompt(choice.reference)
                self.state_dir.mkdir(parents=True, exist_ok=True)
                export_model_state(state, path)
                log.info("Voice %s built in %.2fs and saved", choice.key, time.perf_counter() - t)
            self._states[choice.key] = state
            return state

    def say(self, choice: VoiceChoice, text: str) -> np.ndarray:
        """Speech for `text` in this voice: mono float32 at the model's rate."""
        with self._lock:
            state = self.state(choice)
            speech = self.model.generate_audio(state, text)
        return np.asarray(speech.detach().cpu().numpy(), dtype=np.float32).reshape(-1)

    def prepare(self, choices: list[VoiceChoice]) -> None:
        """Builds (or loads) every voice in the list, e.g. in the background
        at start-up. A voice that fails is logged and skipped."""
        for c in choices:
            try:
                self.state(c)
            except Exception:
                log.exception("Couldn't prepare voice %s", c.key)


def common_voices(library: VoiceLibrary) -> list[VoiceChoice]:
    """One choice per Oblivion.esm voice type, for building at first run."""
    out = []
    for v in library.voice_types():
        if v.plugin != "oblivion.esm":
            continue
        try:
            out.append(VoiceChoice(v.key, f"{v.race.title()} {v.sex}", library.reference_path(v)))
        except LookupError as e:
            log.info("Skipping voice %s: %s", v.key, e)
    return out
