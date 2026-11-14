#pragma once

#include "json.h"

class TESObjectREFR;
struct OBSECommandTableInterface;

// NPC awareness, part 1 (Phase 5 step 4; MVXK21, Nov 7: seated readers didn't
// know they were reading). Read with the game's own condition functions (the
// same ones vanilla dialogue tests) and the AI process, at the moment the
// player starts talking, before the hold changes anything.
namespace lc::awareness
{
	void Init(const OBSECommandTableInterface* commands);

	// What the NPC is doing: {"sitting", "sleeping", "package", "reading",
	// "using", "weapon_out"}, raw values as the game gives them (the companion
	// words them).
	lc::json::Value Activity(TESObjectREFR* npc);

	// What the NPC has equipped: [{"name", "kind"}], kind one of "one-handed
	// blade", "two-handed blade", "one-handed blunt weapon", "two-handed blunt
	// weapon", "staff", "bow", "weapon", "arrows", "shield", "heavy armor",
	// "light armor", "clothing". MVXK21 (Nov 11 playtest of 0.21.3): an
	// Argonian with a bow on his back denied having one.
	lc::json::Value Equipment(TESObjectREFR* npc);

	// What the NPC can see of the player: {"sees", "weapon_out", "sneaking",
	// "in_combat", "health_pct", "armor_rating", "clothing_value", "swimming",
	// and from 0.21.6 "sex", "race" and "equipment" (as Equipment() below)}.
	lc::json::Value PlayerAsSeenBy(TESObjectREFR* npc);
}
