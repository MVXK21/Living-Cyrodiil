#pragma once

// Which playthrough this is, for memory (Phase 5 step 2, docs/phase5-plan.md).
//
// When the game saves, a small file named after the save is written to
// Documents\My Games\Oblivion\LivingCyrodiil\saves\ with the playthrough's id
// and its memory number: every exchange the companion remembers takes the
// next number. When a save loads, that file says which memories had happened
// by then; later ones are set aside, so NPCs forget what was said after that
// save (MVXK21, Nov 7).
//
// Not the xOBSE co-save: xOBSE only writes a plugin's co-save data if the
// plugin has an assigned opcode base, which this plugin doesn't (it adds no
// script commands). Plugin 0.15.0 tried it and xOBSE skipped the save (Nov 9).
//
// A save with no file (made before this feature, or renamed outside the
// game) gets an id made from the character's name, race and sex, and an
// unknown memory number: then nothing is set aside.

#include <string>

namespace lc::playthrough
{
	// The memory number when it isn't known: keep every memory.
	constexpr long long kUnknownSeq = -1;

	void BeforeLoad();                          // PreLoadGame: forget the old one
	void OnLoad(const std::string& saveName);   // LoadGame: the save's file name
	void AfterLoad();                           // PostLoadGame: make sure there's an id
	void OnSave(const std::string& savePath);   // SaveGame: the full path of the .ess
	void Reset();                               // back to the main menu

	// Makes sure there's an id (a new game before its first save). Returns
	// true if it was just created.
	bool Ensure();

	const std::string& Id();
	long long Seq();                            // kUnknownSeq if not known

	// The companion remembered something: `seq` is its newest number.
	void SetSeq(const std::string& id, long long seq);
}
