#pragma once

// Diagnostic for Phase 2 step 6: logs which voice files the game looks for,
// so we can see the exact path it expects for our placeholder line.
//
// It patches the game's import table for a few Windows file functions
// (CreateFileA, GetFileAttributesA, GetFileAttributesExA, FindFirstFileA).
// Each patched function logs paths containing "voice" and then calls the
// original, so the game behaves exactly as before. Logging stops after a
// fixed number of lines to keep the log small.

namespace lc::filewatch
{
	void Install();
}
