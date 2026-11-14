#include "npc_sheet.h"
#include "log.h"
#include "chatter.h"
#include "awareness.h"
#include "quests.h"

#include "obse/GameAPI.h"
#include "obse/GameData.h"
#include "obse/GameForms.h"
#include "obse/GameMenus.h"
#include "obse/GameObjects.h"
#include "obse/GameExtraData.h"

#include <cstdio>
#include <string>

namespace lc::npc
{
	namespace
	{
		// Breadcrumb: the step being worked on, so a crash report names it.
		volatile const char* g_stage = "idle";
		inline void Stage(const char* s) { g_stage = s; }

		std::string SafeName(TESForm* form)
		{
			if (!form) return {};
			const char* n = GetFullName(form);
			if (!n || !*n || strcmp(n, "<no name>") == 0) return {};
			return n;
		}

		std::string HexId(UInt32 id)
		{
			char buf[16];
			sprintf_s(buf, "%08X", id);
			return buf;
		}

		std::string ModOf(UInt32 formId)
		{
			UInt32 index = formId >> 24;
			if (index == 0xFF)
				return "(created in game)";
			if (!g_dataHandler || !*g_dataHandler)
				return {};
			const char* mod = (*g_dataHandler)->GetNthModName(index);
			return mod ? mod : "";
		}

		// Calls Actor vtable slot 0x89 ("GetDisposition" in xOBSE's headers).
		// Confirmed in game (v0.3.2, Nov 2): it takes ONE argument, the actor the
		// disposition is toward, and returns it in EAX. xOBSE's header declares two
		// arguments; calling it that way shifted our stack by 4 bytes and crashed
		// the next line (v0.3.1, LivingCyrodiil.dll+0x7E7A).
		// ESP is still saved and restored around the call as a safety net, and
		// stackPopped reports the bytes the callee removed (expected: 4).
		int CallDispositionSlot(void* fn, Actor* self, Actor* with, int* stackPopped)
		{
			int result = 0;
			unsigned espBefore = 0, espAfter = 0;
			__asm
			{
				mov  espBefore, esp
				push with             // the one argument
				mov  ecx, self        // __thiscall: 'this' in ECX
				call fn
				mov  result, eax
				mov  espAfter, esp
				mov  esp, espBefore   // undo whatever the callee did to the stack
			}
			*stackPopped = static_cast<int>(espAfter - (espBefore - 4));
			return result;
		}

		// Disposition toward the player, 0-100, or -1 if it can't be read.
		// Crash-guarded and range-checked; any result outside 0-100 is treated as
		// unknown. Verified against the console's GetDisposition in game.
		int ReadDisposition(Actor* actor)
		{
			if (!actor || !g_thePlayer || !*g_thePlayer)
				return -1;
			static bool s_loggedSignature = false;
			int value = -1;
			int popped = 0;
			__try
			{
				void** vtable = *reinterpret_cast<void***>(actor);
				value = CallDispositionSlot(vtable[0x89], actor, *g_thePlayer, &popped);
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_WARN("Reading disposition crashed (exception %08X); leaving it out", GetExceptionCode());
				return -1;
			}
			if (!s_loggedSignature || popped != 4)
			{
				s_loggedSignature = true;
				if (popped == 4)
					LC_MSG("Disposition slot: returned %d (1 argument, as expected)", value);
				else
					LC_WARN("Disposition slot removed %d bytes, expected 4: the game function may differ "
						"from the one tested. Value %d", popped, value);
			}
			if (value < 0 || value > 100)
			{
				LC_WARN("Disposition read as %d, outside 0-100; leaving it out", value);
				return -1;
			}
			return value;
		}

		void AddLocation(TESObjectREFR* ref, lc::json::Value& sheet)
		{
			TESObjectCELL* cell = ref->parentCell;
			if (!cell)
				return;
			const bool interior = cell->IsInterior();
			std::string cellName = SafeName(cell);
			std::string world = interior ? std::string() : SafeName(cell->worldSpace);

			sheet.Set("interior", interior);
			if (!cellName.empty()) sheet.Set("cell", cellName);
			if (!world.empty()) sheet.Set("worldspace", world);
			// Walled towns are worlds of their own with the open world as their
			// parent (Leyawiin, Bravil, the Imperial City districts, Shivering
			// Isles towns; modded cities too). Outdoors in one means on its
			// streets, inside the walls (Nov 5 playtest: Betto Plotius, on
			// Leyawiin's Main Street, talked as if he were out on the road).
			if (!interior && cell->worldSpace && cell->worldSpace->parentWorldspace)
				sheet.Set("in_town", true);

			// Where outdoors, for naming the place from the game's map markers
			// (lore pass, Nov 10: the Waterfront and the open country have no
			// names of their own). The companion finds the nearest marker.
			if (!interior && cell->worldSpace)
				sheet.Set("pos_x", static_cast<double>(ref->posX)).Set("pos_y", static_cast<double>(ref->posY))
					.Set("world_id", HexId(cell->worldSpace->refID));

			// One human-readable place name for the prompt.
			std::string place = !cellName.empty() ? cellName : world;
			if (!place.empty())
				sheet.Set("location", place);
		}

		// The 21 skills in the game's order (kActorVal_Armorer onwards), with the
		// names players know them by.
		const int kSkillCount = 21;
		const char* const kSkillNames[kSkillCount] = {
			"Armorer", "Athletics", "Blade", "Block", "Blunt", "Hand to Hand", "Heavy Armor",
			"Alchemy", "Alteration", "Conjuration", "Destruction", "Illusion", "Mysticism", "Restoration",
			"Acrobatics", "Light Armor", "Marksman", "Mercantile", "Security", "Sneak", "Speechcraft",
		};

		// Phase 5 step 3a: the world right now and the player's standing.
		// Weather classification flags as in the WTHR record (UESP): 1 pleasant,
		// 2 cloudy, 4 rainy, 8 snow. The raw value is sent too, to confirm in game.
		void AddWorld(lc::json::Value& sheet)
		{
			Stage("weather");
			Sky* sky = Sky::GetSingleton();
			TESWeather* weather = sky ? sky->firstWeather : nullptr;
			if (weather)
			{
				const UInt8 c = weather->precipType;
				const char* kind = (c & 0x08) ? "snow" : (c & 0x04) ? "rain" : (c & 0x02) ? "cloudy"
					: (c & 0x01) ? "clear" : "";
				if (*kind)
					sheet.Set("weather", kind);
				sheet.Set("weather_raw", static_cast<int>(c));
			}

			Stage("player standing");
			PlayerCharacter* player = g_thePlayer ? *g_thePlayer : nullptr;
			if (!player)
				return;
			lc::json::Value standing;
			// The bounty two ways (Nov 10 playtest: thefts seen by NPCs, bounty
			// still 0): the Bounty actor value, and the player's crime gold extra
			// data. Both are logged; the higher one is used.
			const int bountyAv = static_cast<int>(player->GetActorValue(kActorVal_Bounty));
			int crimeGold = -1;
			if (auto* extra = static_cast<ExtraCrimeGold*>(player->baseExtraList.GetByType(kExtraData_CrimeGold)))
				crimeGold = static_cast<int>(extra->crimeGold);
			LC_MSG("Character sheet: player bounty %d (actor value), crime gold %s", bountyAv,
				crimeGold < 0 ? "none" : std::to_string(crimeGold).c_str());
			standing.Set("fame", static_cast<int>(player->GetActorValue(kActorVal_Fame)))
				.Set("infamy", static_cast<int>(player->GetActorValue(kActorVal_Infamy)))
				.Set("bounty", bountyAv > crimeGold ? bountyAv : crimeGold);
			TESNPC* base = player->baseForm ? OBLIVION_CAST(player->baseForm, TESForm, TESNPC) : nullptr;
			if (base)
			{
				const bool female = base->actorBaseData.IsFemale();
				lc::json::Value factions = lc::json::Value::MakeArray();
				for (auto* entry = &base->actorBaseData.factionList; entry; entry = entry->next)
				{
					auto* data = entry->data;
					if (!data || !data->faction || data->rank < 0)
						continue;                  // rank -1: expelled or left
					TESFaction* f = data->faction;
					std::string fname = SafeName(f);
					if (fname.empty() || f->IsHidden())
						continue;
					lc::json::Value fv;
					fv.Set("name", fname).Set("rank", static_cast<int>(data->rank));
					const char* title = f->GetNthRankName(data->rank, female);
					if (title && *title)
						fv.Set("rank_title", title);
					factions.Push(std::move(fv));
				}
				standing.Set("factions", std::move(factions));
			}
			sheet.Set("player", std::move(standing));
		}

		void FillSheet(TESObjectREFR* ref, Actor* actor, TESNPC* npc, lc::json::Value& sheet, std::string& name)
		{
			Stage("name");
			name = SafeName(ref);
			if (name.empty()) name = SafeName(npc);

			Stage("sex");
			const bool female = npc->actorBaseData.IsFemale();
			Stage("ids and plugin");
			sheet.Set("ref_id", HexId(ref->refID))
				.Set("base_id", HexId(npc->refID))
				.Set("plugin", ModOf(npc->refID))
				.Set("name", name)
				.Set("sex", female ? "female" : "male");
			Stage("race");
			sheet.Set("race", npc->race.race ? SafeName(npc->race.race) : std::string());
			// The race whose voice recordings this NPC uses (Dark Elves use High
			// Elf voices, Khajiit Argonian...). Left out when it's their own race.
			Stage("voice race");
			if (TESRace* race = npc->race.race)
			{
				TESRace* voice = race->voiceRaces[female ? 1 : 0];
				if (voice && voice != race)
				{
					std::string v = SafeName(voice);
					if (!v.empty())
						sheet.Set("voice_race", v);
				}
			}
			Stage("class");
			sheet.Set("class", npc->npcClass ? SafeName(npc->npcClass) : std::string());

			// Skill levels and class details, for how much this NPC knows about
			// each subject (Phase 2 step 7, lore depth). Read from the NPC's base
			// record: plain stored fields, no game function calls.
			Stage("skills");
			lc::json::Value skills = lc::json::Value::MakeObject();
			int total = 0;
			for (int i = 0; i < kSkillCount; ++i)
			{
				const int v = npc->skillLevels[i];
				total += v;
				skills.Set(kSkillNames[i], v);
			}
			if (total > 0)
				sheet.Set("skills", std::move(skills));
			Stage("class details");
			if (TESClass* cls = npc->npcClass)
			{
				const UInt32 spec = cls->specialization;
				if (spec < 3)
					sheet.Set("specialization", spec == 0 ? "combat" : spec == 1 ? "magic" : "stealth");
				lc::json::Value majors = lc::json::Value::MakeArray();
				for (UInt32 av : cls->majorSkills)
					if (av >= kActorVal_Armorer && av < kActorVal_Armorer + kSkillCount)
						majors.Push(kSkillNames[av - kActorVal_Armorer]);
				sheet.Set("major_skills", std::move(majors));
			}

			// Base-record factions. Changes made during play (joining, expulsion)
			// aren't included yet; that's Phase 5 live game state.
			Stage("factions");
			lc::json::Value factions = lc::json::Value::MakeArray();
			for (auto* entry = &npc->actorBaseData.factionList; entry; entry = entry->next)
			{
				auto* data = entry->data;
				if (!data || !data->faction)
					continue;
				TESFaction* f = data->faction;
				std::string fname = SafeName(f);
				if (fname.empty())
					continue;   // nameless factions are internal plumbing
				lc::json::Value fv;
				fv.Set("name", fname).Set("rank", static_cast<int>(data->rank));
				const char* title = f->GetNthRankName(data->rank, female);
				if (title && *title)
					fv.Set("rank_title", title);
				if (f->IsHidden())
					fv.Set("hidden", true);   // used by scripts, not a real membership
				factions.Push(std::move(fv));
			}
			sheet.Set("factions", std::move(factions));

			Stage("disposition");
			int disposition = ReadDisposition(actor);
			if (disposition >= 0)
				sheet.Set("disposition", disposition);

			Stage("location");
			AddLocation(ref, sheet);
			Stage("game hour");
			sheet.Set("hour", static_cast<double>(TimeGlobals::GameHour()));
			// Phase 5: days since the game began (with the hour as a fraction),
			// so memory knows how long ago things were said; and the calendar
			// date (month 0 = Morning Star).
			Stage("game date");
			sheet.Set("days_passed", static_cast<double>(TimeGlobals::GameDaysPassed())
					+ static_cast<double>(TimeGlobals::GameHour()) / 24.0)
				.Set("day", static_cast<double>(TimeGlobals::GameDay()))
				.Set("month", static_cast<double>(TimeGlobals::GameMonth()))
				.Set("year", static_cast<double>(TimeGlobals::GameYear()));
			AddWorld(sheet);
			// Step 4: what they're doing, and what they can see of the player.
			Stage("activity");
			sheet.Set("activity", lc::awareness::Activity(ref));
			Stage("equipment");
			sheet.Set("equipment", lc::awareness::Equipment(ref));
			Stage("player as seen");
			sheet.Set("player_seen", lc::awareness::PlayerAsSeenBy(ref));
			// Step 3b: quest stages and globals, for what NPCs know of events.
			Stage("quest stages");
			sheet.Set("world_state", lc::quests::Report());
			Stage("recent chatter");
			lc::json::Value chatter = lc::chatter::RecentFor(ref);
			if (!chatter.AsArray().empty())
			{
				LC_MSG("Character sheet: %d line(s) of recent chatter", static_cast<int>(chatter.AsArray().size()));
				sheet.Set("recent_chatter", chatter);
			}
			Stage("done");
		}
	}

	const char* TargetText(Target t)
	{
		switch (t)
		{
		case Target::Ok:         return "NPC";
		case Target::Nothing:    return "nothing";
		case Target::NotAPerson: return "not a person";
		case Target::Creature:   return "a creature";
		case Target::Dead:       return "dead";
		}
		return "?";
	}

	bool PlayerOutdoorPos(float* x, float* y, std::string* worldId)
	{
		if (!g_thePlayer || !*g_thePlayer)
			return false;
		TESObjectCELL* cell = (*g_thePlayer)->parentCell;
		if (!cell || cell->IsInterior() || !cell->worldSpace)
			return false;
		*x = (*g_thePlayer)->posX;
		*y = (*g_thePlayer)->posY;
		*worldId = HexId(cell->worldSpace->refID);
		return true;
	}

	std::string PlayerOutdoorPlace()
	{
		if (!g_thePlayer || !*g_thePlayer)
			return {};
		TESObjectCELL* cell = (*g_thePlayer)->parentCell;
		if (!cell || cell->IsInterior())
			return {};
		return SafeName(cell->worldSpace);
	}

	const char* LastStage() { return const_cast<const char*>(g_stage); }

	Target ReadCrosshairNpc(lc::json::Value& sheet, std::string& name)
	{
		Stage("crosshair");
		if (!g_HUDInfoMenu || !*g_HUDInfoMenu)
			return Target::Nothing;
		return ReadNpc((*g_HUDInfoMenu)->crosshairRef, sheet, name);
	}

	Target ReadDialogSpeaker(lc::json::Value& sheet, std::string& name)
	{
		Stage("dialogue window");
		DialogMenu* menu = static_cast<DialogMenu*>(GetMenuByType(kMenuType_Dialog));
		if (!menu)
			return Target::Nothing;
		return ReadNpc(menu->speaker, sheet, name);
	}

	Target ReadNpc(TESObjectREFR* ref, lc::json::Value& sheet, std::string& name)
	{
		Stage("reference");
		if (!ref || !ref->baseForm)
			return Target::Nothing;

		Stage("form type");
		const UInt8 type = ref->baseForm->typeID;
		if (type == kFormType_Creature)
			return Target::Creature;
		if (type != kFormType_NPC)
			return Target::NotAPerson;

		Stage("casts");
		TESNPC* npc = OBLIVION_CAST(ref->baseForm, TESForm, TESNPC);
		Actor* actor = OBLIVION_CAST(ref, TESObjectREFR, Actor);
		if (!npc || !actor)
			return Target::NotAPerson;

		// A plain stored field (Actor::DeadState, 1 = dead), not a virtual call:
		// v0.3.0 called the unproven IsDead() slot and crashed here or nearby.
		Stage("dead state");
		if (actor->DeadState == 1)
			return Target::Dead;

		sheet = lc::json::Value::MakeObject();
		FillSheet(ref, actor, npc, sheet, name);
		return Target::Ok;
	}
}
