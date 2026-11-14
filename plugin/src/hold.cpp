#include "hold.h"
#include "chatter.h"
#include "log.h"

#include "obse/PluginAPI.h"
#include "obse/GameAPI.h"
#include "obse/GameForms.h"
#include "obse/GameObjects.h"

#include <shlobj.h>

#include <algorithm>
#include <cstdio>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <string>

namespace lc::hold
{
	namespace
	{
		const ULONGLONG kAfterReplyMs = 10000;     // time to answer after a reply
		const ULONGLONG kWaitForReplyMs = 30000;   // key released, reply not here yet
		const ULONGLONG kAfterTapMs = 0;           // a tap or a cancelled talk: let go at once
		const float     kWalkAwayUnits = 500.0f;   // about 7 m: after the reply, walking this far ends it
		const ULONGLONG kDistanceCheckMs = 250;
		// Re-applying the hold (Nov 5 playtest: NPCs walked off mid-conversation;
		// a hold set while the dialogue window was closing didn't take).
		const ULONGLONG kReapplyAfterMs = 600;     // after the hold starts: the dialogue window has closed by then
		const ULONGLONG kWatchMs = 500;            // how often a held NPC's position is checked
		const float     kMovedUnits = 40.0f;       // about half a metre: they're walking
		const ULONGLONG kMinReapplyGapMs = 1000;
		// Turning to face the player (MVXK21, Nov 7: only the head turned).
		const float     kTurnDegPerSec = 240.0f;   // a half turn in about 0.75 s
		const float     kFacingDoneDeg = 3.0f;     // close enough: stop turning
		const float     kRefaceDeg = 25.0f;        // the player moved round this far: turn again
		const ULONGLONG kRefaceCheckMs = 500;
		// A walking NPC isn't turned: turning steers their walk, so they
		// followed the player around (MVXK21, Nov 7 playtest of 0.11.0).
		const float     kStillUnitsPerFrame = 1.0f;  // more than this in a frame: they're moving
		const ULONGLONG kMovingHoldMs = 300;         // ...and count as moving this long after
		const float     kPinSlackUnits = 1.0f;       // pinned: put back once they've drifted this far

		OBSEConsoleInterface* g_console = nullptr;
		void (*g_onRelease)(const std::string&, const char*) = nullptr;
		std::string g_held;                        // ref id of the NPC being held ("" = nobody)
		bool        g_replyPlaying = false;
		bool        g_replyDone = false;           // the reply has been said (walking away now ends it)
		ULONGLONG   g_releaseAt = 0;               // 0 = not scheduled
		ULONGLONG   g_nextDistanceCheck = 0;
		ULONGLONG   g_reapplyAt = 0;               // one more SetRestrained once the window has closed (0 = done)
		ULONGLONG   g_nextWatch = 0;
		ULONGLONG   g_lastReapply = 0;
		float       g_anchor[3] = {};              // where the NPC was when last held
		bool        g_haveAnchor = false;
		int         g_moves = 0;                   // times they were seen walking while held
		// Stopping a walk under way, step by step (each step logged, so the
		// log shows which one works): 1 = restraint off, then on again the
		// next frame; 2 = also their standing idle animation; 3 = pinned where
		// they stand for the rest of the hold.
		int         g_stopStep = 0;
		int         g_stopStepLogged = 0;          // "stopped after step N" said for this step
		bool        g_restrainNextFrame = false;
		bool        g_pinned = false;
		bool        g_aiOff = false;               // step 4: SetActorsAI 0 until released
		float       g_pinPos[3] = {};
		int         g_pinFixes = 0;
		int         g_lastPinFixes = 0;            // g_pinFixes at the last position check
		const int   kWalkInPlaceFixes = 10;        // put back this often in half a second: walking on the spot
		float       g_framePos[3] = {};
		bool        g_haveFramePos = false;
		ULONGLONG   g_movingUntil = 0;

		TESObjectREFR* FindNpc(const std::string& refIdHex)
		{
			char* end = nullptr;
			unsigned long id = strtoul(refIdHex.c_str(), &end, 16);
			if (!end || *end || id == 0)
				return nullptr;
			TESForm* form = LookupFormByID(id);
			TESObjectREFR* ref = form ? OBLIVION_CAST(form, TESForm, TESObjectREFR) : nullptr;
			return ref && ref->baseForm && ref->baseForm->typeID == kFormType_NPC ? ref : nullptr;
		}

		void Run(TESObjectREFR* ref, const char* line)
		{
			const bool ok = g_console && g_console->RunScriptLine2 && g_console->RunScriptLine2(line, ref, true);
			LC_MSG("Hold: %s -> %s", line, ok ? "ran" : "reported failure");
		}

		// Run without a log line (for lines run every frame).
		bool RunQuiet(TESObjectREFR* ref, const char* line)
		{
			return g_console && g_console->RunScriptLine2 && g_console->RunScriptLine2(line, ref, true);
		}

		// ---- who is restrained, on disk ---------------------------------------
		// SetRestrained is stored in saves. If the player saves mid-conversation,
		// the NPC would load frozen. So the restrained NPC's ref id is kept in a
		// small file next to the log, and undone after every load.

		std::string StateFile()
		{
			char docs[MAX_PATH] = {};
			if (FAILED(SHGetFolderPathA(nullptr, CSIDL_PERSONAL, nullptr, 0, docs)))
				return {};
			return std::string(docs) + "\\My Games\\Oblivion\\LivingCyrodiil\\restrained.txt";
		}

		void SaveRestrained(const std::string& refId)
		{
			const std::string path = StateFile();
			if (path.empty())
				return;
			if (refId.empty())
			{
				DeleteFileA(path.c_str());
				return;
			}
			FILE* f = nullptr;
			if (fopen_s(&f, path.c_str(), "w") == 0 && f)
			{
				fputs(refId.c_str(), f);
				fclose(f);
			}
		}

		std::string LoadRestrained()
		{
			const std::string path = StateFile();
			FILE* f = nullptr;
			char buf[32] = {};
			if (path.empty() || fopen_s(&f, path.c_str(), "r") != 0 || !f)
				return {};
			fgets(buf, sizeof(buf), f);
			fclose(f);
			return buf;
		}

		// ---- no greetings while the player talks -------------------------------
		// Nov 5 playtest: NPCs often greeted the player ("Hello", radiant
		// greetings) while they held the talk key and spoke. Two of the game's
		// settings control that: how close the player must be for an NPC to
		// greet them, and how long before an NPC greets again. They're changed
		// in memory only, for the whole conversation, and put back exactly as
		// they were (they're never stored in saves or the ini).
		SettingInfo* g_greetDistance = nullptr;
		SettingInfo* g_greetTimer = nullptr;
		float        g_savedDistance = 0, g_savedTimer = 0;
		bool         g_greetingsOff = false;

		// No C++ objects: runs inside __try.
		bool LookUpGreetingSettings()
		{
			__try
			{
				if (!g_greetDistance)
					GetGameSetting("fAIMinGreetingDistance", &g_greetDistance);
				if (!g_greetTimer)
					GetGameSetting("fAIGreetingTimer", &g_greetTimer);
				return g_greetDistance && g_greetTimer;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				g_greetDistance = g_greetTimer = nullptr;
				return false;
			}
		}

		void GreetingsOff()
		{
			lc::chatter::Off();                  // and no NPC-to-NPC chatter either (Nov 10)
			if (g_greetingsOff)
				return;
			if (!LookUpGreetingSettings())
			{
				LC_WARN("Greetings: couldn't find the game's greeting settings; NPCs may still greet while you talk");
				return;
			}
			g_savedDistance = g_greetDistance->f;
			g_savedTimer = g_greetTimer->f;
			g_greetDistance->f = 0.0f;           // nobody is close enough to be greeted
			g_greetTimer->f = 100000.0f;         // and nobody is due to greet again
			g_greetingsOff = true;
			LC_MSG("Greetings: off while the player talks (were fAIMinGreetingDistance %.1f, fAIGreetingTimer %.1f)",
				g_savedDistance, g_savedTimer);
		}

		void GreetingsOn()
		{
			lc::chatter::On();
			if (!g_greetingsOff)
				return;
			g_greetDistance->f = g_savedDistance;
			g_greetTimer->f = g_savedTimer;
			g_greetingsOff = false;
			LC_MSG("Greetings: back on (fAIMinGreetingDistance %.1f, fAIGreetingTimer %.1f)", g_savedDistance, g_savedTimer);
		}

		// Squared distance from the player to `ref`, or -1 if they're in
		// different places (through a door, another world) or it can't be read.
		// Outdoors the world is a grid of cells, so two people a few steps
		// apart can be in different cells: compare the world, not the cell
		// (Nov 5 playtest: Shomara was let go as "player walked away" while
		// the player stood next to her, then walked off on later exchanges).
		// No C++ objects: runs inside __try.
		float DistanceSquaredToPlayer(TESObjectREFR* ref)
		{
			__try
			{
				TESObjectREFR* player = g_thePlayer ? *g_thePlayer : nullptr;
				if (!player || !ref || !player->parentCell || !ref->parentCell)
					return -1.0f;
				if (player->parentCell != ref->parentCell)
				{
					if (player->parentCell->IsInterior() || ref->parentCell->IsInterior()
						|| player->parentCell->worldSpace != ref->parentCell->worldSpace)
						return -1.0f;                     // somewhere else entirely
				}
				const float dx = player->posX - ref->posX;
				const float dy = player->posY - ref->posY;
				const float dz = player->posZ - ref->posZ;
				return dx * dx + dy * dy + dz * dz;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return -1.0f;
			}
		}

		// The NPC's position. No C++ objects: runs inside __try.
		bool ReadPos(TESObjectREFR* ref, float* out)
		{
			__try
			{
				if (!ref)
					return false;
				out[0] = ref->posX;
				out[1] = ref->posY;
				out[2] = ref->posZ;
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		void SetAnchor(TESObjectREFR* ref)
		{
			g_haveAnchor = ReadPos(ref, g_anchor);
		}

		// ---- turning to face the player ------------------------------------------
		// A held (restrained) NPC's AI doesn't turn them, and "Look player" only
		// turns the head. So while the player talks and the NPC replies, the
		// plugin turns them a little each frame with SetAngle until they face the
		// player. NPCs sitting or lying down are left as they are.

		bool        g_turning = false;
		ULONGLONG   g_lastTurnTick = 0;
		ULONGLONG   g_nextRefaceCheck = 0;
		float       g_lastSetDeg = -1000.0f;     // what we set last frame, to see if it took
		int         g_turnSteps = 0;
		bool        g_turnBroken = false;        // SetAngle didn't take: stop trying for this NPC
		bool        g_loggedSitting = false;

		float Wrap180(float deg)
		{
			while (deg > 180.0f) deg -= 360.0f;
			while (deg <= -180.0f) deg += 360.0f;
			return deg;
		}

		// The NPC's heading, how far they must turn to face the player (degrees,
		// + = clockwise) and whether they're sitting or lying down. No C++
		// objects: runs inside __try.
		bool ReadFacing(TESObjectREFR* ref, float* headingDeg, float* turnDeg, bool* seated)
		{
			__try
			{
				TESObjectREFR* player = g_thePlayer ? *g_thePlayer : nullptr;
				if (!player || !ref)
					return false;
				*seated = ref->GetSitSleepState() != 0;
				const float rad2deg = 57.2957795f;
				// Oblivion's heading: 0 faces north (+Y), turning clockwise (towards +X).
				const float dx = player->posX - ref->posX, dy = player->posY - ref->posY;
				if (dx * dx + dy * dy < 1.0f)
					return false;
				*headingDeg = ref->rotZ * rad2deg;
				*turnDeg = Wrap180(atan2f(dx, dy) * rad2deg - *headingDeg);
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		void StartTurning(const char* why)
		{
			if (g_turnBroken)
				return;
			if (!g_turning)
				LC_MSG("Turn: %s turning to face the player (%s)", g_held.c_str(), why);
			g_turning = true;
			g_lastTurnTick = GetTickCount64();
			g_lastSetDeg = -1000.0f;
			g_turnSteps = 0;
		}

		// One frame of turning.
		void TurnStep(TESObjectREFR* ref, ULONGLONG now)
		{
			float heading = 0, turn = 0;
			bool seated = false;
			if (!ReadFacing(ref, &heading, &turn, &seated))
			{
				g_turning = false;
				return;
			}
			if (now < g_movingUntil && !g_pinned)
			{
				g_lastSetDeg = -1000.0f;  // walking: turning them would steer the walk
				g_turnSteps = 0;          // (and the "did it take" check starts over)
				return;
			}
			if (seated)
			{
				if (!g_loggedSitting)
					LC_MSG("Turn: %s is sitting or lying down; not turning them", g_held.c_str());
				g_loggedSitting = true;
				g_turning = false;
				return;
			}
			// Did last frame's SetAngle take? If the heading hasn't moved at all
			// after a few tries, the game is overriding it: stop and say so.
			if (g_lastSetDeg > -999.0f && fabsf(Wrap180(heading - g_lastSetDeg)) > 1.0f && ++g_turnSteps > 5
				&& fabsf(turn) > kFacingDoneDeg)
			{
				LC_WARN("Turn: SetAngle on %s didn't take (heading %.0f, set %.0f); giving up turning them",
					g_held.c_str(), heading, g_lastSetDeg);
				g_turning = false;
				g_turnBroken = true;
				return;
			}
			if (fabsf(turn) <= kFacingDoneDeg)
			{
				LC_MSG("Turn: %s now faces the player (heading %.0f)", g_held.c_str(), heading);
				g_turning = false;
				return;
			}
			const float dt = std::min(0.1f, (now - g_lastTurnTick) / 1000.0f);
			g_lastTurnTick = now;
			float step = kTurnDegPerSec * dt;
			if (step <= 0.0f)
				return;
			if (step > fabsf(turn))
				step = fabsf(turn);
			float target = heading + (turn > 0 ? step : -step);
			while (target < 0.0f) target += 360.0f;
			while (target >= 360.0f) target -= 360.0f;
			char line[64];
			sprintf_s(line, "SetAngle z %.2f", target);
			if (!(g_console && g_console->RunScriptLine2 && g_console->RunScriptLine2(line, ref, true)) && g_turnSteps == 0)
				LC_MSG("Turn: %s -> reported failure (watching whether it took)", line);
			g_lastSetDeg = target;
		}

		// SetRestrained again: it can be undone (a dialogue window closing
		// after we set it, the game's own AI). Harmless when already set.
		void Reapply(TESObjectREFR* ref, const char* why)
		{
			if (!ref)
				return;
			LC_MSG("Hold: holding %s again (%s)", g_held.c_str(), why);
			Run(ref, "SetRestrained 1");
			g_lastReapply = GetTickCount64();
			SetAnchor(ref);
		}

		void Release(const char* why)
		{
			if (g_held.empty())
				return;
			LC_MSG("Hold: letting %s go (%s); seen walking while held %d time(s)", g_held.c_str(), why, g_moves);
			if (g_onRelease)
				g_onRelease(g_held, why);
			if (TESObjectREFR* ref = FindNpc(g_held))
			{
				if (g_aiOff)
					Run(ref, "SetActorsAI 1");
				Run(ref, "SetRestrained 0");
				Run(ref, "StopLook");
				Run(ref, "EvaluatePackage");     // pick up their schedule again straight away
			}
			g_aiOff = false;
			if (g_stopStep)
				LC_MSG("Hold: stopping walks for %s got to step %d%s", g_held.c_str(), g_stopStep,
					g_pinned ? (g_pinFixes ? " (pinned; put back in place " + std::to_string(g_pinFixes) + " time(s))" : std::string(" (pinned)")).c_str() : "");
			SaveRestrained("");
			GreetingsOn();
			g_held.clear();
			g_replyPlaying = false;
			g_replyDone = false;
			g_releaseAt = 0;
			g_reapplyAt = 0;
			g_haveAnchor = false;
			g_moves = 0;
			g_turning = false;
			g_turnBroken = false;
			g_loggedSitting = false;
			g_stopStep = 0;
			g_stopStepLogged = 0;
			g_restrainNextFrame = false;
			g_pinned = false;
			g_pinFixes = 0;
			g_lastPinFixes = 0;
			g_haveFramePos = false;
			g_movingUntil = 0;
		}

		// Every frame: are they moving, and keep a pinned NPC in place.
		void WatchFrame(TESObjectREFR* ref, ULONGLONG now)
		{
			float pos[3];
			if (!ReadPos(ref, pos))
				return;
			if (g_haveFramePos)
			{
				const float dx = pos[0] - g_framePos[0], dy = pos[1] - g_framePos[1];
				if (dx * dx + dy * dy > kStillUnitsPerFrame * kStillUnitsPerFrame)
					g_movingUntil = now + kMovingHoldMs;
			}
			memcpy(g_framePos, pos, sizeof(g_framePos));
			g_haveFramePos = true;
			if (g_restrainNextFrame)
			{
				g_restrainNextFrame = false;
				Reapply(ref, "walk step 1: restraint on again, a frame after it came off");
			}
			if (g_pinned)
			{
				const float dx = pos[0] - g_pinPos[0], dy = pos[1] - g_pinPos[1];
				if (dx * dx + dy * dy > kPinSlackUnits * kPinSlackUnits)
				{
					char line[64];
					sprintf_s(line, "SetPos x %.2f", g_pinPos[0]);
					RunQuiet(ref, line);
					sprintf_s(line, "SetPos y %.2f", g_pinPos[1]);
					RunQuiet(ref, line);
					if (++g_pinFixes == 1 || g_pinFixes % 100 == 0)
						LC_MSG("Hold: %s pinned: put back in place (%d time(s) so far)", g_held.c_str(), g_pinFixes);
				}
			}
		}

		// Pin them where they stand from the start of the hold (0.21.3). Before,
		// the pin was only the third step after catching them walking, and each
		// catch cost a step or two: MVXK21 (Nov 11 playtest of 0.21.2) saw NPCs
		// take steps towards or around him during long sentences; the log showed
		// travel-package NPCs moving 44-108 units, once per turn, despite
		// SetRestrained. NPCs sitting or lying down aren't pinned (their
		// position shifts as they settle into the seat or bed).
		void PinFromStart(TESObjectREFR* ref)
		{
			if (g_pinned || !ref)
				return;
			bool seated = false;
			__try { seated = ref->GetSitSleepState() != 0; }
			__except (EXCEPTION_EXECUTE_HANDLER) { return; }
			if (seated)
			{
				LC_MSG("Hold: %s is sitting or lying down; not pinned", g_held.c_str());
				return;
			}
			if (!ReadPos(ref, g_pinPos))
				return;
			g_pinned = true;
			g_lastPinFixes = 0;
			LC_MSG("Hold: %s pinned where they stand (%.0f, %.0f) until the conversation ends",
				g_held.c_str(), g_pinPos[0], g_pinPos[1]);
		}

		// They were seen walking while held: the next step to stop them.
		void StopWalk(TESObjectREFR* ref)
		{
			if (g_stopStep < 3)
				++g_stopStep;
			switch (g_stopStep)
			{
			case 1:
				LC_MSG("Hold: stopping %s's walk, step 1: restraint off now, on again next frame", g_held.c_str());
				Run(ref, "SetRestrained 0");
				g_restrainNextFrame = true;
				break;
			case 2:
				LC_MSG("Hold: %s still walking, step 2: standing idle animation, restraint again", g_held.c_str());
				Run(ref, "PlayGroup Idle 1");
				Reapply(ref, "walk step 2");
				break;
			default:
				if (!g_pinned && ReadPos(ref, g_pinPos))
				{
					g_pinned = true;
					LC_MSG("Hold: %s still walking, step 3: pinned where they stand (%.0f, %.0f) until the conversation ends",
						g_held.c_str(), g_pinPos[0], g_pinPos[1]);
					Reapply(ref, "walk step 3");
				}
				else if (g_pinned && !g_aiOff)
				{
					// Still walking on the spot after steps 1 and 2: their AI
					// package wants them elsewhere (MVXK21, Nov 11 playtest of
					// 0.21.6: Nahsi's schedule switched to a travel package at
					// 6 pm mid-conversation; she walked on the spot, and her
					// voice didn't start, so every part waited 4 s). Turn this
					// NPC's AI off until the conversation ends.
					LC_MSG("Hold: %s still walking on the spot, step 4: their AI off until the conversation ends", g_held.c_str());
					Run(ref, "SetActorsAI 0");
					g_aiOff = true;
				}
				break;
			}
		}
	}

	void SetReleaseHandler(void (*handler)(const std::string& refIdHex, const char* why))
	{
		g_onRelease = handler;
	}

	void Init(void* consoleInterface)
	{
		g_console = static_cast<OBSEConsoleInterface*>(consoleInterface);
	}

	void Begin(const std::string& refIdHex)
	{
		if (g_held == refIdHex)
		{
			g_releaseAt = 0;           // already held: keep them while the key is down
			g_replyDone = false;
			GreetingsOff();
			Reapply(FindNpc(refIdHex), "talking again");
			PinFromStart(FindNpc(refIdHex));   // if they were seated before and stood up since
			StartTurning("talking again");
			return;
		}
		Release("talking to someone else");
		TESObjectREFR* ref = FindNpc(refIdHex);
		if (!ref)
			return;
		// SetRestrained stops them at once and they can still speak (0.7.3 test).
		// An AI package (AddScriptPackage) never took effect, by name or by form id.
		g_held = refIdHex;
		g_replyPlaying = false;
		g_replyDone = false;
		g_releaseAt = 0;
		SaveRestrained(refIdHex);
		LC_MSG("Hold: %s at the start: %s", refIdHex.c_str(), lc::chatter::Describe(ref).c_str());
		Run(ref, "SetRestrained 1");
		Run(ref, "Look player");
		g_moves = 0;
		SetAnchor(ref);
		PinFromStart(ref);
		g_reapplyAt = GetTickCount64() + kReapplyAfterMs;
		GreetingsOff();
		LC_MSG("Hold: holding %s", refIdHex.c_str());
		StartTurning("player started talking");
	}

	void KeyReleased(unsigned long long heldMs, bool cancelled)
	{
		if (g_held.empty() || g_replyPlaying)
			return;
		const bool tap = cancelled || heldMs < 300;
		g_releaseAt = GetTickCount64() + (tap ? kAfterTapMs : kWaitForReplyMs);
	}

	void ReplyStarted(const std::string& refIdHex)
	{
		if (g_held != refIdHex)
			Begin(refIdHex);
		if (g_held != refIdHex)
			return;
		g_replyPlaying = true;
		g_replyDone = false;
		g_releaseAt = 0;
		Reapply(FindNpc(refIdHex), "reply starting");
		StartTurning("reply starting");
	}

	void ReplyEnded()
	{
		if (g_held.empty())
			return;
		g_replyPlaying = false;
		g_replyDone = true;
		g_releaseAt = GetTickCount64() + kAfterReplyMs;
	}

	void Tick()
	{
		if (g_held.empty())
			return;
		const ULONGLONG now = GetTickCount64();
		if (g_releaseAt && now >= g_releaseAt)
		{
			Release(g_replyDone ? "conversation over" : "no reply came");
			return;
		}
		// Once the dialogue window has had time to close, hold them again.
		if (g_reapplyAt && now >= g_reapplyAt)
		{
			g_reapplyAt = 0;
			if (TESObjectREFR* ref = FindNpc(g_held))
			{
				Reapply(ref, "after the hold started");
				Run(ref, "Look player");
			}
		}

		if (TESObjectREFR* ref = FindNpc(g_held))
			WatchFrame(ref, now);

		// Turn to face the player, a little each frame; afterwards, turn again
		// if the player has moved round them (not while they're walking).
		if (g_turning)
		{
			if (TESObjectREFR* ref = FindNpc(g_held))
				TurnStep(ref, now);
			else
				g_turning = false;
		}
		else if (!g_turnBroken && now >= g_nextRefaceCheck)
		{
			g_nextRefaceCheck = now + kRefaceCheckMs;
			float heading = 0, turn = 0;
			bool seated = false;
			TESObjectREFR* ref = FindNpc(g_held);
			if (ref && now >= g_movingUntil && ReadFacing(ref, &heading, &turn, &seated) && !seated
				&& fabsf(turn) > kRefaceDeg)
				StartTurning("the player moved round them");
		}

		// Watch where they are: walking while held means the hold came off.
		if (now >= g_nextWatch)
		{
			g_nextWatch = now + kWatchMs;
			TESObjectREFR* ref = FindNpc(g_held);
			float pos[3];
			if (ref && ReadPos(ref, pos))
			{
				if (!g_haveAnchor)
				{
					memcpy(g_anchor, pos, sizeof(g_anchor));
					g_haveAnchor = true;
				}
				const float dx = pos[0] - g_anchor[0], dy = pos[1] - g_anchor[1];
				const float moved = sqrtf(dx * dx + dy * dy);
				// Pinned but being put back again and again: their AI is walking
				// them on the spot. Try the first two steps to stop the walk.
				const int fixes = g_pinFixes - g_lastPinFixes;
				g_lastPinFixes = g_pinFixes;
				if (g_pinned && fixes >= kWalkInPlaceFixes && !g_aiOff && now - g_lastReapply >= kMinReapplyGapMs)
				{
					++g_moves;
					LC_WARN("Hold: %s is walking on the spot while pinned (put back %d times in %llu ms; %s)",
						g_held.c_str(), fixes, kWatchMs,
						g_replyPlaying ? "during the reply" : g_replyDone ? "after the reply" : "while the player talks");
					StopWalk(ref);
				}
				else if (moved > kMovedUnits && now - g_lastReapply >= kMinReapplyGapMs)
				{
					++g_moves;
					LC_WARN("Hold: %s moved %.0f units while held (%s)", g_held.c_str(), moved,
						g_replyPlaying ? "during the reply" : g_replyDone ? "after the reply" : "while the player talks");
					// A plain SetRestrained 1 didn't stop a walk already under way
					// (0.10.2 log), and off-and-on plus EvaluatePackage in one
					// frame didn't either (0.11.0 log, Nov 7: Otumeel walked 25
					// times). EvaluatePackage may even restart the walk, so it's
					// gone; StopWalk tries stronger steps in turn.
					StopWalk(ref);
				}
				else if (g_stopStep > g_stopStepLogged && moved <= kMovedUnits && now - g_lastReapply >= kMinReapplyGapMs)
				{
					g_stopStepLogged = g_stopStep;
					LC_MSG("Hold: %s stopped walking after step %d", g_held.c_str(), g_stopStep);
				}
			}
		}

		// After the reply, walking away ends the conversation at once.
		if (g_replyDone && now >= g_nextDistanceCheck)
		{
			g_nextDistanceCheck = now + kDistanceCheckMs;
			const float d2 = DistanceSquaredToPlayer(FindNpc(g_held));
			if (d2 < 0)
				Release("player went somewhere else");
			else if (d2 > kWalkAwayUnits * kWalkAwayUnits)
			{
				char why[64];
				sprintf_s(why, "player walked away, %.0f units", sqrtf(d2));
				Release(why);
			}
		}
	}

	const std::string& Held() { return g_held; }

	bool AiOff() { return !g_held.empty() && g_aiOff; }

	void Forget()
	{
		if (!g_held.empty())
			LC_MSG("Hold: forgetting %s (save loading or game closing)", g_held.c_str());
		GreetingsOn();
		g_held.clear();               // restrained.txt stays, so AfterLoad can undo it
		g_replyPlaying = false;
		g_replyDone = false;
		g_releaseAt = 0;
	}

	void AfterLoad()
	{
		// If someone was restrained when the game last saved or closed, free them.
		const std::string stuck = LoadRestrained();
		if (stuck.empty())
			return;
		if (TESObjectREFR* ref = FindNpc(stuck))
		{
			LC_MSG("Hold: %s was restrained when the game last saved or closed; freeing them", stuck.c_str());
			Run(ref, "SetActorsAI 1");        // in case their AI was off (0.21.7, walk step 4)
			Run(ref, "SetRestrained 0");
			Run(ref, "EvaluatePackage");
			SaveRestrained("");
		}
		// Not loaded in this save: keep the file and try after the next load.
	}
}
