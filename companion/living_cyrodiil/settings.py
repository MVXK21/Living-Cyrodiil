"""Load companion settings from settings.local.toml.

The API key can also come from the LC_LLM_API_KEY environment variable,
which wins over the file. The key is never logged.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import logging
import re
import shutil

from .paths import API_KEY_FILE, RELEASE, SETTINGS_EXAMPLE, SETTINGS_FILE

log = logging.getLogger(__name__)

PLACEHOLDER_KEY = "PASTE_YOUR_OPENROUTER_KEY_HERE"
KEY_FILE_PLACEHOLDER = "PASTE_YOUR_KEY_ON_THIS_LINE"
KEY_FILE_TEMPLATE = f"""Living Cyrodiil: your AI service key

1. Get an OpenRouter key at https://openrouter.ai/keys (it starts with sk-or-).
2. Replace the last line of this file with your key, and save.
3. Start the game. The key stays on your PC.

{KEY_FILE_PLACEHOLDER}
"""


def read_key_file(path: Path | None) -> str:
    """The key in the release's key file: its last non-empty line, unless
    that's still the placeholder ("" if none)."""
    if path is None or not path.exists():
        return ""
    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    except OSError:
        return ""
    key = lines[-1] if lines else ""
    return "" if not key or " " in key or key == KEY_FILE_PLACEHOLDER else key


def _write_settings_key(path: Path, key: str) -> None:
    """Keep a copy of the key in the settings file (release), so an update
    that replaces the mod folder doesn't lose it."""
    try:
        text = path.read_text(encoding="utf-8-sig")
        new, n = re.subn(r'(?m)^(\s*api_key\s*=\s*)".*?"', lambda m: f'{m.group(1)}"{key}"', text, count=1)
        if n and new != text:
            path.write_text(new, encoding="utf-8")
    except OSError as e:
        log.warning("Couldn't keep a copy of the API key in %s: %s", path, e)


def prepare_release_files(path: Path = SETTINGS_FILE, key_file: Path | None = API_KEY_FILE) -> None:
    """Release only: create the settings file and the key file on first run,
    and keep the key in both (MVXK21, Nov 13: install, open the mod folder, paste
    the key, play)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        shutil.copyfile(SETTINGS_EXAMPLE, path)
        log.info("Created %s", path)
    if key_file is None:
        return
    in_file = read_key_file(key_file)
    try:
        saved = tomllib.loads(path.read_text(encoding="utf-8-sig")).get("llm", {}).get("api_key", "")
    except (OSError, tomllib.TOMLDecodeError):
        saved = ""
    if in_file:
        if in_file != saved:
            _write_settings_key(path, in_file)
    elif saved and saved != PLACEHOLDER_KEY:
        # The mod folder was replaced (an update): put the key back.
        try:
            key_file.write_text(KEY_FILE_TEMPLATE.replace(KEY_FILE_PLACEHOLDER, saved), encoding="utf-8")
            log.info("Put the saved API key back into %s", key_file)
        except OSError as e:
            log.warning("Couldn't write %s: %s", key_file, e)
    elif not key_file.exists():
        try:
            key_file.write_text(KEY_FILE_TEMPLATE, encoding="utf-8")
        except OSError as e:
            log.warning("Couldn't create %s: %s", key_file, e)


class SettingsError(Exception):
    """A settings problem the player can fix. The message says how."""


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.8
    max_tokens: int = 250
    timeout_seconds: float = 30.0
    reasoning: bool = False     # let the model think before answering (slower; OpenRouter only)

    def __repr__(self) -> str:  # never print the key
        return (f"LLMSettings(base_url={self.base_url!r}, model={self.model!r}, "
                f"temperature={self.temperature}, max_tokens={self.max_tokens}, "
                f"timeout_seconds={self.timeout_seconds}, reasoning={self.reasoning}, api_key=<hidden>)")


@dataclass(frozen=True)
class SpeechSettings:
    # false = no microphone or speech-to-text at all (type with the type key
    # instead); neither is loaded.
    enabled: bool = True
    engine: str = "whisper"
    whisper_model: str = "base.en"
    moonshine_model: str = "base"
    mic_device: int | str | None = None
    keep_mic_open: bool = False
    # Transcribe at pauses while the key is held (step 4b-3), so the text is
    # usually ready the moment the key comes up.
    transcribe_while_talking: bool = True


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = True
    # Pocket TTS config (relative to the companion folder). Ours points the
    # cloning weights at an ungated mirror (docs/phase3-plan.md).
    config: str = "data/tts/pocket_english_2026-04.yaml"
    # Seconds after the talk key is released with no reply yet before the
    # NPC says a short filler sound ("Hmm."). 0 = never. Normal replies
    # arrive in about 2-2.6 s (MVXK21, Nov 6: only when the LLM lags).
    filler_after_seconds: float = 3.0
    # Lip-sync (Phase 4): a .lip file next to each voice file. "model" = mouth
    # shapes learned from the game's own lip files (built once at first run;
    # until then the mouth follows the loudness). "test" = the spike's test
    # pattern (jaw open and shut three times a second). "off" = none.
    lip_sync: str = "model"
    # No CPU-thread setting: Pocket TTS runs on 1 thread by design, and more
    # threads were no faster on MVXK21's 12-thread CPU (Nov 5: 1-2 threads
    # 3.7x real time, 6 threads 3.1x).


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings
    speech: SpeechSettings = SpeechSettings()
    voice: VoiceSettings = VoiceSettings()
    game_port: int = 47833
    setting_profile: str = "vanilla"
    log_level: str = "INFO"
    knowledge_file: str = "world_knowledge.txt"   # relative to the companion folder, or absolute
    # What NPCs know of the world (step 3c): "both" (quest progress and the
    # world knowledge file, the file winning), "automatic" (quest progress only)
    # or "manual" (the file only).
    world_mode: str = "both"
    game_data_folder: str = ""                    # Oblivion's Data folder; "" = find it in the registry

    @property
    def has_api_key(self) -> bool:
        key = self.llm.api_key
        return bool(key) and key != PLACEHOLDER_KEY


def load_settings(path: Path = SETTINGS_FILE, require_key: bool = True) -> Settings:
    if RELEASE and path == SETTINGS_FILE:
        prepare_release_files(path)
    if not path.exists():
        raise SettingsError(
            f"Settings file not found: {path}\n"
            "Run companion\\setup.ps1 first; it creates this file from settings.example.toml."
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path.name} has a typo: {e}") from e

    llm = data.get("llm", {})
    key = (os.environ.get("LC_LLM_API_KEY")
           or (read_key_file(API_KEY_FILE) if RELEASE and path == SETTINGS_FILE else "")
           or llm.get("api_key", ""))
    if require_key and (not key or key == PLACEHOLDER_KEY):
        where = API_KEY_FILE if RELEASE else path
        raise SettingsError(f"No API key yet. Open {where} in Notepad, paste your OpenRouter key and save.")

    try:
        llm_settings = LLMSettings(
            base_url=str(llm.get("base_url", "https://openrouter.ai/api/v1")),
            api_key=key,
            model=str(llm.get("model", "anthropic/claude-haiku-5.5")),
            temperature=float(llm.get("temperature", 0.8)),
            max_tokens=int(llm.get("max_tokens", 250)),
            timeout_seconds=float(llm.get("timeout_seconds", 30)),
            reasoning=bool(llm.get("reasoning", False)),
        )
    except (TypeError, ValueError) as e:
        raise SettingsError(f"A number in [llm] in {path.name} isn't valid: {e}") from e

    sp = data.get("speech", {})
    mic = sp.get("mic_device", "")
    if isinstance(mic, str) and mic.strip().isdigit():
        mic = int(mic)
    speech = SpeechSettings(
        engine=str(sp.get("engine", "whisper")).lower(),
        whisper_model=str(sp.get("whisper_model", "base.en")),
        moonshine_model=str(sp.get("moonshine_model", "base")),
        mic_device=mic if mic not in ("", None) else None,
        enabled=bool(sp.get("enabled", True)),
        keep_mic_open=bool(sp.get("keep_mic_open", False)),
        transcribe_while_talking=bool(sp.get("transcribe_while_talking", True)),
    )
    if speech.engine not in ("whisper", "moonshine"):
        raise SettingsError(f'[speech] engine must be "whisper" or "moonshine", not "{speech.engine}"')

    try:
        port = int(data.get("game", {}).get("port", 47833))
    except (TypeError, ValueError) as e:
        raise SettingsError(f"[game] port in {path.name} must be a number: {e}") from e
    if not 1024 <= port <= 65535:
        raise SettingsError(f"[game] port must be between 1024 and 65535, not {port}")

    vo = data.get("voice", {})
    try:
        voice = VoiceSettings(
            enabled=bool(vo.get("enabled", True)),
            config=str(vo.get("config", VoiceSettings.config)),
            filler_after_seconds=float(vo.get("filler_after_seconds", VoiceSettings.filler_after_seconds)),
            lip_sync=str(vo.get("lip_sync", VoiceSettings.lip_sync)).strip().lower(),
        )
    except (TypeError, ValueError) as e:
        raise SettingsError(f"A value in [voice] in {path.name} isn't valid: {e}") from e
    if voice.lip_sync not in ("model", "test", "off"):
        raise SettingsError(f'[voice] lip_sync must be "model", "test" or "off", not "{voice.lip_sync}"')

    return Settings(
        llm=llm_settings,
        speech=speech,
        voice=voice,
        game_port=port,
        setting_profile=str(data.get("world", {}).get("setting_profile", "vanilla")),
        knowledge_file=str(data.get("world", {}).get("knowledge_file", "world_knowledge.txt")),
        world_mode=str(data.get("world", {}).get("mode", "both")).lower(),
        game_data_folder=str(data.get("game", {}).get("data_folder", "")),
        log_level=str(data.get("logging", {}).get("level", "INFO")),
    )
