# Living Cyrodiil companion

The Python program that runs alongside the game: speech-to-text, prompts, LLM calls, voices (Pocket TTS), lip-sync and memory. It runs on Python 3.14 in its own virtual environment (`.venv`), so it doesn't touch any other Python on your PC.

## Setup (once)

```
powershell -ExecutionPolicy Bypass -File companion\setup.ps1
```

Then open `companion\settings.local.toml` in Notepad and paste your OpenRouter key into `api_key`. That file stays on your PC and is never committed.

## Running with the game

```
powershell -ExecutionPolicy Bypass -File companion\companion.ps1
```

Start it before or after the game; the plugin connects by itself and reconnects if you restart either one. Leave the window open while you play. It shows game events as they arrive. How the two talk is documented in `docs\protocol.md`.

In game: hold **F4** at someone and speak, or press **F3** and type (keys are set in the plugin's `LivingCyrodiil.ini`).

**First run:** the companion builds a few things from your own game files, in the background, and saves them in `companion\cache\`: voice references (seconds), the lip-sync model (about 10 minutes; until it's done, mouths just follow the loudness) and its gesture bank. Later starts load them at once. The speech-to-text and voice models download once into `companion\models\`.

## Type-to-chat test (no game needed)

```
powershell -ExecutionPolicy Bypass -File companion\chat.ps1
```

Pick a sample NPC and type. Commands: `/npc` (switch NPC), `/reset` (forget the conversation), `/prompt` (show the NPC's instructions), `/quit`.

## Speech-to-text test (no game needed)

```
powershell -ExecutionPolicy Bypass -File companion\stt_test.ps1
```

Press Enter, speak, press Enter again. The same clip runs through Whisper base, Whisper small and Moonshine base, showing what each heard and how long it took. Options: `-devices` (list mics), `-keepopen` (keep the mic open between clips), `-replay` (re-run saved clips). Clips and results go to `companion\recordings\` (git-ignored). The first run downloads the models (about 850 MB) into `companion\models\`.

## Files you might edit

| File | What it controls |
| --- | --- |
| `settings.local.toml` | API key, model, temperature, reply length limit; `[speech]` (on or off, engine, microphone, transcribing while the key is held); `[voice]` (on or off, filler sounds, `lip_sync`) |
| `world_knowledge.txt` | Your own facts about your world, numbered entries switched on and off |
| `memory\<playthrough>\*.json` | What each NPC remembers of you, one file per NPC per playthrough. Plain JSON: read it, edit it, or delete it to make them forget |
| `data/setting_vanilla.toml` | World facts, ground rules, race speech patterns, banned words, fallback lines, speech-to-text vocabulary |
| `data/sample_npcs.toml` | Made-up NPCs for testing without the game |

## Logs and tests

* Log: `companion\logs\companion.log`. Set `level = "DEBUG"` in settings to also log every full prompt.
* Tests (offline, free): `companion\.venv\Scripts\python.exe -m pytest companion\tests`
