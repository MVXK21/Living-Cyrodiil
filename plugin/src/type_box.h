#pragma once

// Typed input (start of Phase 5): the game's own text-entry window
// (TextEditMenu, the one character creation uses for your name), opened
// while looking at an NPC, so the player can type what they say.
//
// Spike (plugin 0.13.0): opens the window, follows what's typed and logs
// how it closed, to settle docs/typed-input-research.md's unknowns before
// it's wired to the companion. Main thread only.

#include <string>

namespace lc::typebox
{
	// Opens the window with this prompt ("Say to Witseidutsei:"). Returns
	// false (and logs why) if the game wouldn't open it.
	bool Open(const std::string& prompt);

	bool IsOpen();

	// Call every frame. When the window has just closed, returns true and
	// fills in the last text it held and whether it was sent (Enter or the
	// OK button) rather than cancelled (Escape or Back).
	bool Tick(std::string* text, bool* sent);
}
