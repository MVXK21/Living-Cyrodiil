#pragma once

// Keeps the NPC being talked to in place (Phase 2, pulled forward from
// Phase 5's schedule pausing by MVXK21, Nov 3, 2026).
//
// When the player starts talking, the NPC is restrained (SetRestrained 1:
// stops at once, can still speak) and looks at the player. They're let go
// 10 s after their reply ends, as soon as the player walks away after the
// reply, at once after a tap, 30 s after the key is released if no reply
// comes, and at once if the player starts talking to someone else.
// Restraint is stored in saves, so the restrained NPC is recorded in
// restrained.txt next to the log and freed after the next load.
//
// Main thread only.

#include <string>

namespace lc::hold
{
	void Init(void* consoleInterface);    // OBSEConsoleInterface*

	void Begin(const std::string& refIdHex);         // talk key pressed on this NPC
	void KeyReleased(unsigned long long heldMs, bool cancelled);
	void ReplyStarted(const std::string& refIdHex);  // NPC begins saying a reply
	void ReplyEnded();                               // NPC finished the reply

	void Tick();                                     // lets the NPC go when it's time

	void Forget();        // a save is loading or the game is closing: drop state, run nothing
	void AfterLoad();     // a save finished loading: free anyone left restrained

	// Told when a held NPC is let go at the end of a conversation (the reply
	// is over, the player walked away or talked to someone else), with why.
	// Not on loads or quitting. Phase 5: the companion summarises the talk.
	const std::string& Held();                       // ref id of the NPC being held ("" = nobody)
	// The held NPC's AI is off (walk step 4). The game's IsTalking then stays
	// "yes" for them (Nov 12 playtest: No-Coins Draninus), so it can't be trusted.
	bool AiOff();
	void SetReleaseHandler(void (*handler)(const std::string& refIdHex, const char* why));
}
