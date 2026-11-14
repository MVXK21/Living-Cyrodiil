#pragma once

#include "json.h"

struct OBSECommandTableInterface;

// Quest-stage awareness (Phase 5 step 3b, docs/phase5-3b-events.md): every
// started quest's current stage, its stages done, and whether it's complete,
// plus every global with a non-zero value (KvatchDestroyed and the like),
// read live from the game by editor ID, so mod quests are reported too. The
// companion decides what each one means for what NPCs know.
namespace lc::quests
{
	void Init(const OBSECommandTableInterface* commands);

	// {"quests": {"MQ01": {"stage": 100, "done": [10, 15], "completed": true}, ...},
	//  "globals": {"KvatchDestroyed": 1, ...}}. Main thread only.
	lc::json::Value Report();
}
