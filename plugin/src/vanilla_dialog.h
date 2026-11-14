#pragma once

// Remembering vanilla dialogue (Phase 2 step 8): while the game's own dialogue
// window is open, every line the NPC says there is passed to the companion as
// a vanilla_line message, so a voice conversation can follow on from it.
//
// The window's text box (DialogMenu::dialogText) is read each frame; when its
// text changes, the new line is reported, with the topic the player clicked:
// the topic tile under the mouse (or keyboard focus) as the text changed.
// The greeting has no topic. Main thread only.

#include <functional>
#include <string>

namespace lc::vanilla
{
	// Called with (npc ref id hex, npc name, topic, line text), all UTF-8.
	using LineHandler = std::function<void(const std::string&, const std::string&,
		const std::string&, const std::string&)>;

	void SetHandler(LineHandler handler);
	void Tick();      // call every frame
}
