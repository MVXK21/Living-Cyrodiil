# Plugin ↔ companion protocol

How the game plugin (C++, inside Oblivion) and the companion (Python) talk. This is the only contract between them. A future Oblivion Remastered port needs a new game connector that speaks this protocol, and nothing on the companion side changes.

**Protocol version: 1** (Phase 2 step 3). Bump it on any incompatible change.

## Transport

* **TCP on `127.0.0.1` only.** The companion listens and the plugin connects. Default port **47833**, set in both `companion/settings.local.toml` (`[game] port`) and `OBSE/Plugins/LivingCyrodiil.ini` (`[Companion] Port`).
* **One JSON object per line**, UTF-8, ending in `\n`. No line may exceed **64 KB**. Longer lines are dropped and logged by the receiver.
* Every message has a string field **`type`**. Receivers **ignore unknown types and unknown fields** (log them, don't fail), so either side can add things without breaking the other.
* One game connection at a time. If a second one arrives, the companion closes the older one.
* The plugin reconnects on its own every few seconds if the companion isn't running or closes. The game never waits on the connection.

## Handshake

The plugin sends `hello` as soon as it connects; the companion answers with its own `hello`.

```json
{"type": "hello", "protocol": 1, "side": "plugin", "plugin_version": "0.2.0", "game": "oblivion"}
{"type": "hello", "protocol": 1, "side": "companion", "companion_version": "0.1.0"}
```

If `protocol` doesn't match, the receiver logs a clear error, sends `error` with `code: "protocol_mismatch"`, and closes. Nothing else is sent before the handshake completes.

## Messages

### Plugin → companion

| `type` | Fields | When |
| --- | --- | --- |
| `hello` | see above | On connect |
| `event` | `name` (string), optional `detail` (string) | Game events worth logging: `save_loaded`, `save_saved`, `main_menu`, `game_exit` |
| `talk_start` | `npc` (character sheet, below) | The player pressed the talk key while looking at a living NPC |
| `talk_end` | `ref_id` (string), `held_ms` (number) | The talk key was released normally. The companion transcribes what was recorded |
| `talk_cancel` | `ref_id` (string), `reason` (string) | The talk was interrupted (a menu or the console opened, a save started loading, the game is closing). The companion discards the recording |
| `type_start` | `npc` (object, the character sheet, as in `talk_start`) | Plugin 0.14.0 (Phase 5). The player pressed the type key at this NPC and the text box is open. The NPC is held from now on. The companion gets the NPC's voice and the LLM connection ready while the player types. Nothing else; if the box is cancelled, no message follows |
| `talk_text` | `ref_id` (string), `npc` (object, the character sheet), `text` (string, what was typed, UTF-8 from the game's text box) | Plugin 0.14.0 (Phase 5). The player typed a line to this NPC and sent it (Enter or OK). The companion answers it exactly like a transcribed spoken line (no microphone or speech-to-text), with a `reply` and its audio as usual. The plugin holds the NPC until the reply has come and ended, as after `talk_end` |
| `talk_over` | `ref_id` (string), `reason` (string) | Plugin 0.16.0 (Phase 5 memory). The plugin let a held NPC go: the reply was over and the player didn't speak again, walked away, or talked to someone else (not on loads or quitting). The companion summarises what was said since the last summary (one small LLM call). Talking to them again within an in-game hour carries on the same conversation; later it's a new meeting that recalls the earlier ones |
| `playthrough` | `id` (string), `seq` (number), `save` (string, the save file's name) | Plugin 0.15.0 (Phase 5 memory). Which playthrough the loaded save belongs to and how much of its memory it had, from the save's record in `Documents\My Games\Oblivion\LivingCyrodiil\saves\<save name>.txt` (since 0.15.1; 0.15.0 tried the xOBSE co-save). Sent after each load, after the handshake when in game, and when a new game's first conversation creates an id. A save with no record gets an id made from the character's name, race and sex (`char-...`) and `seq` -1: the number isn't known, so the companion keeps every memory and replies with `memory_seq` saying where numbering stands. New games get a random id (`pt-...`) and `seq` 0. Otherwise the companion sets aside remembered exchanges numbered after `seq` |

The companion records the microphone from `talk_start` until `talk_end` or `talk_cancel`. Holds under 0.3 s count as taps and aren't transcribed. In step 5 the companion answers a transcript with `notify` ("You said: …"); step 6 replaces that with the NPC's reply.

| `vanilla_line` | `ref_id` (string), `name` (string), `text` (string), optional `topic` (string) | The NPC said `text` in the game's own dialogue window (step 8). Sent each time the window's text changes, including the greeting (no `topic`). `topic` is the topic the player clicked, read from the topic under the cursor or keyboard focus as the text changed. The companion adds these to that NPC's conversation history in order, for the current visit |

### Character sheet

Read live from the running game at the moment the key is pressed, so NPCs added or changed by mods work. Fields marked *optional* are left out when the game can't provide them; receivers must cope with any of them missing.

| Field | Type | Example | Notes |
| --- | --- | --- | --- |
| `ref_id` | string | `"0001A2B3"` | The NPC's reference in the world (hex) |
| `base_id` | string | `"0001A2B2"` | Their base NPC record (hex) |
| `plugin` | string | `"Oblivion.esm"` | Mod file the NPC record comes from; `"(created in game)"` for spawned NPCs |
| `name`, `race`, `class` | string | `"Witseidutsei"`, `"Argonian"`, `"Publican"` | As shown in game |
| `sex` | string | `"female"` | `"male"` or `"female"` |
| `voice_race` | string, *optional* | `"High Elf"` | The race whose voice recordings this NPC's race uses for their sex (the race record's voice setting: vanilla Dark Elves use High Elf voices). Left out when it's their own race. The companion picks the NPC's TTS voice from it (plugin 0.9.2) |
| `factions` | array | `[{"name": "Fighters Guild", "rank": 2, "rank_title": "Swordsman"}]` | From the NPC record. `hidden: true` marks script-only factions, which the companion ignores. Changes made during play are not included yet (Phase 5) |
| `disposition` | number, *optional* | `62` | Toward the player, 0–100. Left out if it couldn't be read |
| `interior` | bool | `true` | |
| `cell`, `worldspace`, `location` | string, *optional* | `"Three Sisters' Inn"` | `location` is the best single place name |
| `in_town` | bool, *optional* | `true` | Plugin 0.10.2. Outdoors inside a walled town or city: the NPC's worldspace has a parent world (every vanilla city, the Imperial City districts, Shivering Isles towns, and modded cities built the same way). Left out otherwise |
| `last_outdoor_place` | string, *optional* | `"Leyawiin"` | Interiors only: the last outdoor place the player was in, since interiors don't record their city |
| `hour` | number | `20.5` | Game time, 0–24 |
| `days_passed` | number | `47.85` | Plugin 0.16.0 (Phase 5). In-game days since the game began, with the hour as a fraction; dates memories |
| `day`, `month`, `year` | number | `27`, `7`, `433` | Plugin 0.16.0. The calendar date; `month` 0 = Morning Star |
| `weather` | string | `"rain"` | Plugin 0.17.0 (Phase 5). The current weather's classification: `clear`, `cloudy`, `rain` or `snow` (left out if unknown). `weather_raw` (number) is the raw classification byte, sent while the flag reading is being confirmed in game |
| `player` | object | | Plugin 0.17.0 (Phase 5). The player's standing: `fame`, `infamy`, `bounty` (numbers) and `factions` (as the NPC's, without hidden ones and without ranks of -1, which mean expelled or left). The companion decides who knows what (DESIGN.md, live game state) |
| `recent_chatter` | array | | Plugin 0.18.0 (left out if empty). Lines this NPC said to another NPC, or heard from them, in the last 3 minutes, oldest first. Each: `by` (`"self"` or `"other"`), `other_name`, `other_ref` (hex), `seconds_ago` (number). Only lines someone answered count as chatter. What was said isn't sent: 0.18.0 and 0.18.1 also sent `plugin`, `file` and `plugin_path` (the line's vanilla voice file), dropped in 0.18.2 as unreliable; the companion ignores them |
| `pos_x`, `pos_y`, `world_id` | number, number, string | `17000`, `44000`, `"0000003C"` | Plugin 0.19.0, outdoors only: the NPC's position and the world's form id (hex). The companion names places with no name of their own (the Imperial City Waterfront, villages, the open country) from the nearest map marker in the player's Oblivion.esm (`places.py`) |
| `last_outdoor_x`, `last_outdoor_y`, `last_outdoor_world_id` | number, number, string | | Plugin 0.19.0, indoors only: where the player last was outdoors, for interiors in places with no name of their own (the Bloated Float, an inn in the wilds) |
| `activity` | object | `{"sitting": 3, "sleeping": 0, "package": 8, "reading": true}` | Plugin 0.20.0: what the NPC was doing when the player started talking, from the game's condition functions (`GetSitting`, `GetSleeping`, `GetCurrentAIPackage`; -1 = couldn't be read) and the AI process's item in use (`reading` for a book, `using` its name otherwise). Plugin 0.21.5 adds `weapon_out` (the NPC's own `IsWeaponOut`) |
| `equipment` | array | `[{"name": "Steel Bow", "kind": "bow"}, {"name": "Fur Cuirass", "kind": "light armor"}]` | Plugin 0.21.5: the NPC's equipped items (worn in their inventory). `kind`: `one-handed blade`, `two-handed blade`, `one-handed blunt weapon`, `two-handed blunt weapon`, `staff`, `bow`, `weapon`, `arrows`, `shield`, `heavy armor`, `light armor`, `clothing` (rings and amulets count as clothing; other items are left out) |
| `player_seen` | object | `{"sees": 1, "weapon_out": 1, "sneaking": 0, "in_combat": 0, "health_pct": 80, "armor_rating": 42, "clothing_value": 15, "swimming": 0}` | Plugin 0.20.0: what the NPC can see of the player (`GetDetected`, `IsWeaponOut`, `IsSneaking`, `IsInCombat`, `GetArmorRating`, `GetClothingValue`, `IsSwimming`, health as a percentage). Plugin 0.21.6 adds `sex` (`male`/`female`), `race` (its in-game name) and `equipment` (the player's equipped items, as the NPC's `equipment` below) |
| `world_state` | object | `{"quests": {"MQ06": {"stage": 100, "done": [10, 50, 100], "completed": true}}, "globals": {"KvatchDestroyed": 1}}` | Plugin 0.21.0: every started quest (by editor ID, mod quests too) with its current stage, the stages done (the game's `GetStageDone`) and whether it's complete, and every global with a non-zero value. The companion turns them into what NPCs know (`world_events.py`, `data/quest_events.toml`) |
| `skills` | object, *optional* | `{"Armorer": 78, "Blunt": 52, ...}` | All 21 skill levels, from the NPC's base record. Left out if they all read as 0. Used for how much the NPC knows about each subject |
| `specialization` | string, *optional* | `"combat"` | The NPC's class specialization: `"combat"`, `"magic"` or `"stealth"` |
| `major_skills` | array, *optional* | `["Armorer", "Blunt", ...]` | The class's seven major skills |

### Companion → plugin

| `type` | Fields | When |
| --- | --- | --- |
| `hello` | see above | Reply to the plugin's hello |
| `notify` | `text` (string) | Show `text` to the player as a top-left notification. Used for status and problems ("API key rejected") |
| `reply` | `ref_id` (string), `text` (string), optional `chunks` (array of strings) | The NPC with this reference id says the reply to the player as real dialogue subtitles, one per chunk (1–4; more are merged into the last). Without `chunks`, `text` is one chunk. The plugin writes a silent voice file per chunk sized to its reading time, then says the chunks as parts one after another: for each it sets the `LCPart` global, runs `SayTo player LCReply 1` on the NPC (the placeholder line from `LivingCyrodiil.esp` for that part), swaps the placeholder subtitle for the chunk, and starts the next part when the subtitle clears. General Subtitles are switched on for the reply if the player has them off. If the NPC can't be found, or no subtitle shows within 3 s, `text` is shown as a notification instead. Optional (Phase 3): `voiced` (bool) and `turn` (number): a `reply_audio` per chunk follows with the same `turn`, and each part waits for its audio (below). `listener` (string, `"player"` for now; Phase 6 NPC conversations will name another NPC). `filler` (bool, plugin 0.10.4): a short sound ("Hmm.") the companion sends when the real reply is slow; it has its own `turn` and one `reply_audio`. A real reply for the same NPC that arrives while the filler plays waits, keeping any of its `reply_audio` that arrives meanwhile, and starts as soon as the filler ends. A filler arriving while a real reply plays is ignored. `streaming` (bool, plugin 0.11.0, step 4b): `chunks` holds part 1 only, sent the moment the companion has it while the LLM is still writing; `reply_parts` follows with every part. A streamed reply waiting behind a filler keeps any `reply_parts` and audio that arrive meanwhile |
| `reply_parts` | `ref_id` (string), `turn` (number), `chunks` (array of strings, part 1 first and unchanged) | Plugin 0.11.0. The whole list of parts of a `streaming` reply, sent once the LLM has finished, always before any later part's `reply_audio`. Up to 4 parts (more are merged into the last); a list of one means the reply is just part 1. The plugin writes the later parts' silent voice files and says them after part 1. If part 1 has finished and no `reply_parts` has come within 10 s, the reply ends there. Ignored unless `ref_id` and `turn` match the streamed reply showing now or waiting behind a filler |
| `reply_audio` | `ref_id` (string), `turn` (number), `part` (1-based number), then either `path` (string, a full path to an MP3, UTF-8) and `seconds` (number), or `error` (string) | Phase 3, plugin 0.10.0. Part `part` of a voiced reply, spoken in the NPC's cloned voice: an MP3 like the game's own (44.1 kHz mono 64 kbps) in the companion's staging folder (`companion\cache\audio\`), written completely before this is sent. Sent in part order, as each is generated, after `reply`. When a part is due, the plugin waits up to 10 s for its audio, copies the MP3 over the part's voice files and keeps the subtitle up for `seconds` plus 0.3 s. With `error`, or no audio in time, the part is said silently as an unvoiced one. Ignored unless `ref_id` and `turn` match the voiced reply showing now. Optional `lip` (string, plugin 0.12.0, Phase 4): a full path to the part's `.lip` file (UTF-8; `""` = none), next to the MP3 with the same name. The plugin copies it next to each of the part's voice files along with the MP3, so the game moves the NPC's mouth with the speech; the silent voice files written for a new reply remove any `.lip` left from the last one |
| `memory_seq` | `id` (string), `seq` (number) | Plugin 0.15.0 (Phase 5 memory). The companion saved an exchange to memory, or (after a `playthrough` with `seq` -1) says where numbering stands; `seq` is that number. The plugin keeps the highest one for this playthrough and writes it into the save's record when the game saves. Ignored if `id` isn't the current playthrough |
| `error` | `code` (string), `text` (string) | Something went wrong; the plugin logs it and shows `text` |

Coming in later steps: `status` (thinking, listening).

## Rules both sides follow

* **Log every message sent and received**, in `LivingCyrodiil.log` and `companion.log`.
* **Never block the game.** In the plugin, socket work runs on a background thread. Anything that touches the game (notifications, NPCs) happens on the main thread through the per-frame tick.
* **Text shown in game is plain text.** The plugin escapes characters Oblivion's `Message` command treats specially (`"` and `%`) and strips control characters.
