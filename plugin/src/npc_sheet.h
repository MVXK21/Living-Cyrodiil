#pragma once

// Reads the NPC under the crosshair and builds their character sheet, live
// from the running game, so NPCs added or changed by other mods just work.
// Main thread only: it reads game data.

#include "json.h"

#include <string>

class TESObjectREFR;

namespace lc::npc
{
	enum class Target
	{
		Ok,            // a living NPC; sheet filled in
		Nothing,       // nothing under the crosshair
		NotAPerson,    // an object, door, item...
		Creature,      // a creature (talking to creatures is out of scope)
		Dead,          // a dead NPC
	};

	// Looks at the crosshair. On Ok, fills `sheet` (see docs/protocol.md,
	// "Character sheet") and `name`.
	Target ReadCrosshairNpc(lc::json::Value& sheet, std::string& name);

	// The same for the NPC the vanilla dialogue window is open with.
	Target ReadDialogSpeaker(lc::json::Value& sheet, std::string& name);

	// The same for any reference.
	Target ReadNpc(TESObjectREFR* ref, lc::json::Value& sheet, std::string& name);

	const char* TargetText(Target t);   // for logs
	const char* LastStage();            // which step ReadCrosshairNpc reached (for crash reports)

	// Name of the outdoor place the player is in right now (e.g. "Leyawiin"),
	// or "" when indoors. Called every so often to remember which city an
	// interior belongs to, since interiors don't record that themselves.
	std::string PlayerOutdoorPlace();

	// The player's position outdoors and the world's form id (hex); false
	// indoors. Remembered for interiors, which don't say where they are.
	bool PlayerOutdoorPos(float* x, float* y, std::string* worldId);
}
