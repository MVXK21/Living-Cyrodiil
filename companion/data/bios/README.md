# Living Cyrodiil bios

Short bios for about 110 key characters (rulers and their courts, guild hall
heads, innkeepers, merchants, the Arena's people and other well-known
figures). Each bio is given only to that NPC, as "who you are", matched by
the NPC's name (titles like Count or Brother are ignored) and the plugin that
defines them (`companion/living_cyrodiil/bios.py`).

## Licence and credit

The bios are adapted from the **Unofficial Elder Scrolls Pages (UESP)**,
https://en.uesp.net, character pages (`Oblivion:<name>`), and are licensed
under the **Creative Commons Attribution-ShareAlike 2.5** licence
(https://creativecommons.org/licenses/by-sa/2.5/), the same as UESP's text.
They are rewritten in our own words and condensed. This licence covers only
the files in this folder, not the mod's code.

## Rules

* Written to the NPC: "You are ...".
* Public side and character only: no secrets (who is a vampire, a necromancer,
  a thief or one of the Blades), no quest spoilers, no quoted dialogue. A bio
  shapes how the NPC talks, so a secret in it would leak into conversation.
* True at any point in the game, since bios don't change with quest progress.

```toml
[[bio]]
name = "Velus Hosidius"
plugin = "Oblivion.esm"
text = "You are an Imperial innkeeper who runs the Merchants Inn ..."
```
