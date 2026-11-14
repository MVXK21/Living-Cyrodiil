# Living Cyrodiil <version> (early alpha)

Talk to anyone in Cyrodiil, out loud or by typing, and they answer in their own voice, in character, with what they'd really know: their town, their guild, the news, what they're doing, and what they can see of you. They remember you next time.

**This is an early alpha.** It works, but expect bugs and rough edges. It will be updated often. Please report what you find (see "Reporting bugs" below).

Copyright (c) 2026 MVXK21. All rights reserved. See COPYRIGHT.md.

## What you need

* Oblivion (GOTY or Deluxe, patch 1.2.0416) on Windows 10 or 11, 64-bit.
* **xOBSE** 22 or later.
* **An OpenRouter account and API key** (https://openrouter.ai/keys). NPC replies come from an AI model you pay for yourself, per use: with the recommended model a reply costs roughly a tenth of a cent. Keep an eye on your balance.
* About 3 GB of disk space, and a reasonably modern CPU (the voices are made on your PC).
* A microphone, if you want to speak. Without one, type instead.

## Install (Mod Organizer 2)

1. **Install a new mod from an archive** and pick this zip. Tick the mod, and `LivingCyrodiil.esp` in the plugin list (load order doesn't matter).
2. Right-click the mod, **Open in Explorer**, and open **`PASTE YOUR API KEY HERE.txt`**. Replace its last line with your OpenRouter key and save.
3. Press **F5** in MO2 to refresh, and start the game through xOBSE as usual.

That's all: the mod starts its companion program in the background with the game, and closes it with the game.

**Vortex or by hand:** the zip's top level is the mod folder. Put its contents in `Oblivion\Data` (or let Vortex do it) and enable `LivingCyrodiil.esp`. The key file is then `Oblivion\Data\PASTE YOUR API KEY HERE.txt`.

**Keep the path short.** Windows breaks some of the companion's files past 260 characters. Normal MO2 and game folders are fine; a mods folder buried very deep is not. The game tells you if this happens.

**The first start is slow:** in the background the companion downloads its speech and voice models (about 1 GB), reads your Oblivion.esm, builds the voices from your game's own voice files and learns lip-sync from your game's lip files (about 10 minutes). Until then replies may be subtitles only. Later starts are quick.

## Playing

* **Speak:** look at someone and **hold F4** while you talk; let go when you're done.
* **Type:** look at someone, press **F3**, type, press **Enter** (Escape cancels).
* **From the vanilla dialogue window:** hold F4 while it's open, and carry on talking to that person.
* People stop and face you while you talk, and go back to their day when you're done. Talk again within an in-game hour and it's the same conversation; later, they remember what you talked about.

Try: ask an innkeeper for the local news, a guard about crime in the city, a mage about the schools of magic, anyone about the weather, or what they make of you.

## Settings

* **Keys and in-game notices:** `OBSE\Plugins\LivingCyrodiil.ini` in the mod folder (`TalkKey`, `TypeKey`, any key by name).
* **Everything else** (AI model, microphone on or off, voices on or off): `Documents\My Games\Oblivion\LivingCyrodiil\settings.toml`, created on the first start. An in-game options menu is planned.
* **World knowledge:** `Documents\My Games\Oblivion\LivingCyrodiil\world_knowledge.txt`. Numbered entries you switch on and off to shape your own version of Cyrodiil (for example "the Emperor is alive and the Crisis hasn't begun", for alternative starts). Instructions are at the top of the file; changes apply straight away.
* Your memories, settings and logs live in that Documents folder, so updating or reinstalling the mod never wipes them.

## Updating

Install the new zip over the old one in MO2. If MO2 asks, either choice works: your key is kept.

## Reporting bugs

1. Double-click **`Collect logs.bat`** in the mod folder. It puts `LivingCyrodiil-logs.zip` on your desktop.
2. Post on the mod page's Bugs tab: what you did, what happened, what you expected, roughly when (game time is fine), and attach the zip. Your mod list helps too.

The logs hold what was said in your conversations and your game's details, but never your API key.

## Good to know

* **AI use:** NPC replies are written by the AI model you choose, through OpenRouter. Voices are made on your PC with Pocket TTS, cloned from your own game's voice files for each race and sex. Speech-to-text runs on your PC too (Whisper).
* **Privacy:** what you say to NPCs (as text) and details of the NPC and your game go to your AI provider to get a reply. Your voice recording never leaves your PC.
* **Nothing from Bethesda's files ships with this mod:** voices, books and map details are read from your own game.
* NPCs stay in the setting: Cyrodiil, 3E 433. Ask them about smartphones and they'll look at you strangely.
* Tested on one large modlist (with NorthernUI). Big overhauls that change NPCs, cities and items are read live from your game and should work; tell us if something doesn't.

## Credits

* Lore and character notes adapted from the Unofficial Elder Scrolls Pages (https://en.uesp.net), under CC BY-SA 2.5; those files (`LCCompanion\data\lore` and `bios`) stay under that licence.
* Ideas and some approaches from Sentient Morrowind (see `LCCompanion\third-party`).
* Pocket TTS, Whisper (faster-whisper), PyTorch and the other open-source libraries in `LCCompanion\python`, each under its own licence.
