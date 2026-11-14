# Living Cyrodiil lore library

Short lore entries that NPCs draw on in conversation (DESIGN.md, layer 4,
"Scoped knowledge"). Each conversation gets only the few entries that match
what's being talked about **and** fit the NPC: who would know it (everyone,
locals of a city, members of a faction) and how deep it goes (common,
specialist, scholar).

## Licence and credit

The entries in this folder are adapted from the **Unofficial Elder Scrolls
Pages (UESP)**, https://en.uesp.net, and are licensed under the
**Creative Commons Attribution-ShareAlike 2.5** licence
(https://creativecommons.org/licenses/by-sa/2.5/), the same as UESP's text.
They are rewritten in our own words and condensed; each file lists the UESP
pages it draws on. This licence covers only the files in this folder, not the
mod's code.

In-game book text is never copied here. The game's own books and dialogue
are read from each player's installed game instead (DESIGN.md, source A).

## Rules for entries

* **Knowledge cutoff (MVXK21, Nov 4, 2026):** background lore runs to 3E 432
  inclusive. 3E 433 events are tagged with `quest` and only become known with
  the player's quest progress (Phase 5). Nothing after 3E 433.
* No quest spoilers in common or local knowledge (for example, who secretly
  leads the Mythic Dawn).
* Short: one to three sentences an NPC could plausibly say or know.

## Entry fields

```toml
[[entry]]
id = "leyawiin-count"            # unique, stable
text = "..."                     # the fact, in plain sentences
keywords = ["count", "caro"]     # words that make it relevant (lower case)
known_by = "everyone"            # "everyone", "locals:Leyawiin", "faction:Mages Guild"
depth = "common"                 # "common", "specialist" or "scholar"
area = "magic"                   # optional: expertise area that unlocks deeper entries
always = false                   # optional: always given to NPCs it applies to
quest = ""                       # optional: quest stage that makes it known (Phase 5)
until = ""                       # optional: known only until this happens (Kvatch: "global:KvatchDestroyed")
crisis = false                   # optional: part of the default story's Oblivion Crisis
```

A world knowledge entry that replaces the world facts (`[on, replace]`, the
player's own version of Cyrodiil) leaves out `crisis` and `quest` entries and
keeps `until` entries. So keep crisis facts (the Emperor's murder, the empty
throne) in entries of their own marked `crisis = true`, never inside an
entry that's true either way (Nov 12: an Imperial City guard spoke of the
murder with "the Emperor is alive" switched on). Bios take an optional
`crisis = "..."` sentence the same way.

Depth works with the NPC's expertise (from their skills, class and factions):
everyone can know `common` entries they qualify for; `specialist` entries need
some expertise in the entry's `area`, or belonging to the place or faction the
entry is about; `scholar` entries need deep expertise.
