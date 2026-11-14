#pragma once

// Top-left notifications for the player.
//
// Queue() can be called any time (even from the main menu or a load screen);
// ShowPending() runs on the main thread each frame and displays queued text
// once the player is actually in the game world.

#include <string>

namespace lc::notify
{
	void Init(void* consoleInterface);      // OBSEConsoleInterface*, for RunScriptLine2
	void Queue(const std::string& utf8Text);
	void ShowPending(bool inGame);

	// Exposed for logging/testing: what the game will actually display.
	std::string ToGameText(const std::string& utf8Text);
}
