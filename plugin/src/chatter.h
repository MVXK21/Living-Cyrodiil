#pragma once

#include "json.h"

#include <string>

class TESObjectREFR;
struct OBSECommandTableInterface;

// NPC-to-NPC chatter (MVXK21, Nov 10 playtest: talking to an NPC during or
// just before their radiant conversation failed: SayTo did nothing while
// they were speaking a chatter line).
namespace lc::chatter
{
	void Init(const OBSECommandTableInterface* commands);

	// The game's own IsTalking: 1 = speaking a line now, 0 = not, -1 = unknown.
	int IsTalking(TESObjectREFR* ref);

	// For the log: "talking yes", "talking no" or "talking ?".
	std::string Describe(TESObjectREFR* ref);

	// Someone else within `units` of `speaker` speaking a line now (their chat
	// partner): returns them, or nullptr. Nov 10 playtest: while the partner
	// spoke, the game held our NPC's voice back but showed the subtitle.
	TESObjectREFR* OtherTalkerNear(TESObjectREFR* speaker, float units);

	// No new NPC-to-NPC conversations from now until On(). The game's two
	// chatter-chance settings are changed in memory only and put back exactly.
	void Off();
	void On();

	// ---- who NPCs were just talking to (0.18.0, words dropped in 0.18.2) -----
	// Every 150 ms the NPCs near the player are checked: when one starts a
	// line, it's noted with who they said it to (the NPC beside them who
	// answers). When the player talks to an NPC, their recent lines go in the
	// character sheet ("recent_chatter", docs/protocol.md), so they know they
	// were just talking with someone. The words aren't known: 0.18.0-0.18.1
	// matched lines to the voice files the game looked up, but the game looks
	// up bursts of candidate lines at once, so the matches were unreliable.
	void SetTracking(bool on);
	void Tick();      // main thread, in game
	void Forget();    // a save is loading or the game is closing

	// The NPC's chatter in the last few minutes, oldest first (JSON array;
	// empty if none).
	lc::json::Value RecentFor(TESObjectREFR* ref);

	// The NPC's own chat partner (one of them spoke a line to the other in
	// the last 30 s, and they're beside them), if speaking a line now; else
	// nullptr. MVXK21 (Nov 11 playtest of 0.21.3): a Fighters Guild NPC waited
	// 8 s before every part of his reply because two other NPCs nearby were
	// chatting with each other; only his own partner's line should hold him.
	TESObjectREFR* PartnerTalking(TESObjectREFR* speaker);
}
