#pragma once

// Finds records from LivingCyrodiil.esp in the running game. Their form ids
// depend on the player's load order, so the plugin adds the .esp's current
// mod index to each record's id within the file (tools/build_esp.py).

class TESForm;
class TESGlobal;

namespace lc::esp
{
	constexpr const char* kPluginFile = "LivingCyrodiil.esp";

	// Ids within the .esp (must match tools/build_esp.py).
	constexpr unsigned kPartGlobal = 0x000803;    // LCPart

	// The form, or nullptr if the .esp isn't active or the record is missing.
	// Logs what it found once per id. Main thread only.
	TESForm* Find(unsigned localId, const char* what);
	TESGlobal* FindGlobal(unsigned localId, const char* what);

	// Writes a global's value in memory and checks it took. Never writes files.
	bool SetGlobal(TESGlobal* global, float value);
}
