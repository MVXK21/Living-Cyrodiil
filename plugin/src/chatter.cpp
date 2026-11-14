#include "chatter.h"
#include "log.h"

#include "obse/PluginAPI.h"
#include "obse/CommandTable.h"
#include "obse/GameAPI.h"
#include "obse/GameForms.h"
#include "obse/GameObjects.h"
#include "obse/GameProcess.h"

#include "hold.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace lc::chatter
{
	namespace
	{
		Cmd_Eval     g_isTalking = nullptr;
		SettingInfo* g_chance = nullptr;          // fAISocialchanceForConversation
		SettingInfo* g_chanceInterior = nullptr;  // ...Interior
		float        g_savedChance = 0, g_savedChanceInterior = 0;
		bool         g_off = false;

		// No C++ objects: these run inside __try.
		int EvalIsTalking(TESObjectREFR* ref)
		{
			__try
			{
				double result = 0;
				if (!g_isTalking(ref, nullptr, nullptr, &result))
					return -1;
				return result != 0 ? 1 : 0;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return -1;
			}
		}

		// xOBSE GameProcess.cpp's address for the game's actor process manager
		// (that file isn't compiled into our DLL).
		ActorProcessManager* const kProcessManager = reinterpret_cast<ActorProcessManager*>(0x00B3BD00);

		// Squared distance between two refs in the same place, else -1.
		// No C++ objects: runs inside __try.
		float DistanceSquared(TESObjectREFR* a, TESObjectREFR* b)
		{
			__try
			{
				if (!a || !b || !a->parentCell || !b->parentCell)
					return -1.0f;
				if (a->parentCell != b->parentCell
					&& (a->parentCell->IsInterior() || b->parentCell->IsInterior()
						|| a->parentCell->worldSpace != b->parentCell->worldSpace))
					return -1.0f;
				const float dx = a->posX - b->posX, dy = a->posY - b->posY, dz = a->posZ - b->posZ;
				return dx * dx + dy * dy + dz * dz;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return -1.0f;
			}
		}

		TESObjectREFR* TalkerIn(tList<Actor>* list, TESObjectREFR* speaker, float maxSq)
		{
			__try
			{
				TESObjectREFR* player = g_thePlayer ? *g_thePlayer : nullptr;
				int guard = 0;
				for (tList<Actor>::Iterator it = list->Begin(); !it.End() && guard < 500; ++it, ++guard)
				{
					Actor* a = it.Get();
					if (!a || a == speaker || a == player)
						continue;
					const float d = DistanceSquared(speaker, a);
					if (d < 0 || d > maxSq)
						continue;
					if (EvalIsTalking(a) == 1)
						return a;
				}
				return nullptr;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return nullptr;
			}
		}

		bool LookUpSettings()
		{
			__try
			{
				if (!g_chance)
					GetGameSetting("fAISocialchanceForConversation", &g_chance);
				if (!g_chanceInterior)
					GetGameSetting("fAISocialchanceForConversationInterior", &g_chanceInterior);
				return g_chance || g_chanceInterior;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				g_chance = g_chanceInterior = nullptr;
				return false;
			}
		}
	}

	void Init(const OBSECommandTableInterface* commands)
	{
		const CommandInfo* cmd = commands && commands->GetByName ? commands->GetByName("IsTalking") : nullptr;
		g_isTalking = cmd ? cmd->eval : nullptr;
		if (g_isTalking)
			LC_MSG("Chatter: found the game's IsTalking");
		else
			LC_WARN("Chatter: the game's IsTalking wasn't found; replies can't wait for an NPC's chatter line to end");
	}

	int IsTalking(TESObjectREFR* ref)
	{
		return ref && g_isTalking ? EvalIsTalking(ref) : -1;
	}

	TESObjectREFR* OtherTalkerNear(TESObjectREFR* speaker, float units)
	{
		if (!speaker || !g_isTalking)
			return nullptr;
		const float maxSq = units * units;
		if (TESObjectREFR* t = TalkerIn(&kProcessManager->highActors, speaker, maxSq))
			return t;
		return TalkerIn(&kProcessManager->middleHighActors.head, speaker, maxSq);
	}

	std::string Describe(TESObjectREFR* ref)
	{
		const int talking = IsTalking(ref);
		return talking < 0 ? "talking ?" : talking ? "talking yes" : "talking no";
	}

	void Off()
	{
		if (g_off)
			return;
		if (!LookUpSettings())
		{
			LC_WARN("Chatter: couldn't find the game's conversation settings; NPCs may start chatting while you talk");
			return;
		}
		if (g_chance)
		{
			g_savedChance = g_chance->f;
			g_chance->f = 0.0f;
		}
		if (g_chanceInterior)
		{
			g_savedChanceInterior = g_chanceInterior->f;
			g_chanceInterior->f = 0.0f;
		}
		g_off = true;
		LC_MSG("Chatter: no new NPC conversations while the player talks (were %.1f outdoors, %.1f indoors)",
			g_savedChance, g_savedChanceInterior);
	}

	void On()
	{
		if (!g_off)
			return;
		if (g_chance)
			g_chance->f = g_savedChance;
		if (g_chanceInterior)
			g_chanceInterior->f = g_savedChanceInterior;
		g_off = false;
		LC_MSG("Chatter: NPC conversations back on (%.1f outdoors, %.1f indoors)", g_savedChance, g_savedChanceInterior);
	}

	// ---- what NPCs just said to each other (0.18.0) -------------------------

	namespace
	{
		const ULONGLONG kTickMs = 150;              // how often nearby NPCs are checked
		const float     kEarshotUnits = 2500.0f;    // about 35 m from the player
		const float     kPartnerUnits = 400.0f;     // a chat partner stands this close
		const ULONGLONG kPartnerGapMs = 30000;      // ...and answers within this
		const ULONGLONG kKeepMs = 180000;           // lines are kept 3 minutes
		const size_t    kMaxLines = 24;
		const int       kMaxLogged = 300;
		const int       kMaxSeen = 64;

		struct Line
		{
			UInt32      speaker = 0, listener = 0;
			std::string speakerName, listenerName;
			bool        female = false;
			ULONGLONG   tick = 0;
		};

		bool                g_tracking = false;
		ULONGLONG           g_nextTick = 0;
		std::vector<Line>   g_lines;              // oldest first
		std::vector<UInt32> g_talkingNow;         // who was mid-line at the last check
		int                 g_logged = 0;

		struct Seen
		{
			TESObjectREFR* ref;
			UInt32         id;
			bool           talking;
			bool           female;
		};

		// The NPCs within earshot of the player and whether each is speaking.
		// No C++ objects: runs inside __try.
		int Gather(tList<Actor>* list, Seen* out, int count, int max, TESObjectREFR* player)
		{
			__try
			{
				int guard = 0;
				for (tList<Actor>::Iterator it = list->Begin(); !it.End() && guard < 500 && count < max; ++it, ++guard)
				{
					Actor* a = it.Get();
					if (!a || a == player || !a->baseForm || a->baseForm->typeID != kFormType_NPC)
						continue;
					const float d = DistanceSquared(player, a);
					if (d < 0 || d > kEarshotUnits * kEarshotUnits)
						continue;
					bool dup = false;
					for (int i = 0; i < count; ++i)
						dup = dup || out[i].ref == a;
					if (dup)
						continue;
					TESNPC* npc = static_cast<TESNPC*>(static_cast<TESForm*>(a->baseForm));
					out[count].ref = a;
					out[count].id = a->refID;
					out[count].talking = EvalIsTalking(a) == 1;
					out[count].female = npc->actorBaseData.IsFemale();
					++count;
				}
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
			}
			return count;
		}

		std::string NameOf(TESObjectREFR* ref)
		{
			const char* n = ref && ref->baseForm ? GetFullName(ref->baseForm) : nullptr;
			return n && *n && strcmp(n, "<no name>") != 0 ? n : "someone";
		}

		std::string Hex(UInt32 id)
		{
			char buf[16];
			sprintf_s(buf, "%08X", id);
			return buf;
		}

		TESObjectREFR* RefOf(UInt32 id)
		{
			TESForm* f = id ? LookupFormByID(id) : nullptr;
			return f ? OBLIVION_CAST(f, TESForm, TESObjectREFR) : nullptr;
		}

		void LineStarted(const Seen& s, ULONGLONG now)
		{
			Line line;
			line.speaker = s.id;
			line.speakerName = NameOf(s.ref);
			line.female = s.female;
			line.tick = now;
			// Who it's said to: the NPC beside them who spoke last, not long ago.
			for (auto it = g_lines.rbegin(); it != g_lines.rend(); ++it)
			{
				if (now - it->tick > kPartnerGapMs)
					break;
				if (it->speaker == s.id)
					continue;
				const float d = DistanceSquared(s.ref, RefOf(it->speaker));
				if (d < 0 || d > kPartnerUnits * kPartnerUnits)
					continue;
				line.listener = it->speaker;
				line.listenerName = it->speakerName;
				if (!it->listener)
				{
					it->listener = s.id;              // the line they answered was said to them
					it->listenerName = line.speakerName;
				}
				break;
			}
			if (g_logged++ < kMaxLogged)
				LC_MSG("Chatter: %s (%08X) started a line%s%s", line.speakerName.c_str(), s.id,
					line.listener ? " to " : "", line.listener ? line.listenerName.c_str() : "");
			g_lines.push_back(line);
			if (g_lines.size() > kMaxLines)
				g_lines.erase(g_lines.begin());
		}

	}

	void SetTracking(bool on)
	{
		g_tracking = on;
		LC_MSG("Chatter: awareness %s", on ? "on" : "off (ChatterAwareness=0)");
	}

	void Forget()
	{
		g_lines.clear();
		g_talkingNow.clear();
	}

	void Tick()
	{
		if (!g_tracking || !g_isTalking)
			return;
		const ULONGLONG now = GetTickCount64();
		if (now < g_nextTick)
			return;
		g_nextTick = now + kTickMs;

		TESObjectREFR* player = g_thePlayer ? *g_thePlayer : nullptr;
		if (!player)
			return;
		Seen seen[kMaxSeen];
		int count = Gather(&kProcessManager->highActors, seen, 0, kMaxSeen, player);
		count = Gather(&kProcessManager->middleHighActors.head, seen, count, kMaxSeen, player);

		// Our own replies aren't chatter.
		const std::string& held = lc::hold::Held();
		const UInt32 heldId = held.empty() ? 0 : static_cast<UInt32>(strtoul(held.c_str(), nullptr, 16));

		std::vector<UInt32> talking;
		for (int i = 0; i < count; ++i)
		{
			if (!seen[i].talking)
				continue;
			talking.push_back(seen[i].id);
			bool was = false;
			for (UInt32 id : g_talkingNow)
				was = was || id == seen[i].id;
			if (!was && seen[i].id != heldId)
				LineStarted(seen[i], now);
		}
		g_talkingNow.swap(talking);
		while (!g_lines.empty() && now - g_lines.front().tick > kKeepMs)
			g_lines.erase(g_lines.begin());
	}

	TESObjectREFR* PartnerTalking(TESObjectREFR* speaker)
	{
		if (!speaker || !g_isTalking)
			return nullptr;
		// Without the line tracking there's no telling partners apart: any
		// talker beside them counts (the 0.18-0.21.3 behaviour).
		if (!g_tracking)
			return OtherTalkerNear(speaker, kPartnerUnits);
		const ULONGLONG now = GetTickCount64();
		const UInt32 me = speaker->refID;
		for (auto it = g_lines.rbegin(); it != g_lines.rend(); ++it)
		{
			if (now - it->tick > kPartnerGapMs)
				break;
			UInt32 other = 0;
			if (it->speaker == me)
				other = it->listener;
			else if (it->listener == me)
				other = it->speaker;
			if (!other)
				continue;
			TESObjectREFR* ref = RefOf(other);
			const float d = DistanceSquared(speaker, ref);
			if (ref && d >= 0 && d <= kPartnerUnits * kPartnerUnits && EvalIsTalking(ref) == 1)
				return ref;
		}
		return nullptr;
	}

	lc::json::Value RecentFor(TESObjectREFR* ref)
	{
		lc::json::Value out = lc::json::Value::MakeArray();
		if (!ref)
			return out;
		const ULONGLONG now = GetTickCount64();
		for (const Line& l : g_lines)
		{
			// Only conversations: a line nobody answered (a greeting to the
			// player, a remark to no one) isn't chatter.
			if (!l.listener || now - l.tick > kKeepMs || (l.speaker != ref->refID && l.listener != ref->refID))
				continue;
			const bool self = l.speaker == ref->refID;
			lc::json::Value v;
			v.Set("by", self ? "self" : "other")
				.Set("other_ref", Hex(self ? l.listener : l.speaker))
				.Set("other_name", self ? l.listenerName : l.speakerName)
				.Set("seconds_ago", static_cast<double>((now - l.tick) / 1000));
			out.Push(v);
		}
		return out;
	}
}
