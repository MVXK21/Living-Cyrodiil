# Living Cyrodiil

An LLM-powered conversation mod for The Elder Scrolls IV: Oblivion (original GOTY edition, patch 1.2.0416, with xOBSE). Speak into your microphone to any NPC and hear a lore-grounded, in-character spoken reply.

This repository holds the full source of the mod as released on Nexus Mods, and instructions for building it: see **[BUILD.md](BUILD.md)**.

## What's here

| Folder | What it is |
| --- | --- |
| `plugin/` | The game plugin (C++, 32-bit), built against the xOBSE source. It reports what's happening in the game to the companion and plays the replies. It also starts the companion when the game starts. |
| `companion/` | The companion program (Python 3.14): speech-to-text, prompt building, LLM calls (OpenRouter), voices (Pocket TTS), lip-sync and NPC memory. |
| `companion/data/` | Lore, character bios, world events and the setting's rules. |
| `tools/` | Scripts that build `LivingCyrodiil.esp` and package the release zip. |
| `external/xOBSE` | The xOBSE source, as a git submodule (https://github.com/llde/xOBSE). |
| `docs/` | The plugin-companion protocol (`docs/protocol.md`) and the release README. |

## How it works, in short

The plugin and the companion are separate programs that talk over a local TCP connection (localhost, port 47833), using the JSON messages described in `docs/protocol.md`. The plugin never waits on the network. The companion only connects to:

* the LLM provider the player configures (OpenRouter by default), using the player's own API key;
* Hugging Face, once, to download the speech-to-text and voice models into `Documents\My Games\Oblivion\LivingCyrodiil`.

The release zip bundles a copy of Python 3.14 and the companion's libraries in `LCCompanion\python\`, so players install nothing. The plugin starts it, without a console window, as `LCCompanion\python\python.exe -P -m living_cyrodiil.main` (see `plugin/src/companion_launch.cpp`), and it closes with the game.

## Copyright

Copyright © 2026 MVXK21. All rights reserved; see [COPYRIGHT.md](COPYRIGHT.md). The source is published so that anyone can review and build it. Making it visible here doesn't give permission to copy or redistribute it. The parts that belong to others keep their own licences, as COPYRIGHT.md lists.
