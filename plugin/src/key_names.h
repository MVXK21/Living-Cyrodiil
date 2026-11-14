#pragma once

// Key names for the ini (Phase 2 step 9): "V", "F4", "LeftAlt", "Numpad0",
// "Mouse3"... turned into the codes xOBSE uses: DirectInput scan codes for the
// keyboard, 256 + button for the mouse (256 left, 257 right, 258 middle,
// 259-263 the extra buttons). A plain number still works, as before.

#include <string>

namespace lc::keys
{
	// The key code for `text`, or 0 if it isn't a key name or a valid number.
	// Case, spaces, dashes and underscores are ignored ("left alt" = "LeftAlt").
	unsigned short Parse(const std::string& text);

	// A readable name for a code ("V", "Mouse3"), for the log and messages.
	std::string Name(unsigned short code);
}
