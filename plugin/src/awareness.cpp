#include "awareness.h"
#include "log.h"

#include "obse/PluginAPI.h"
#include "obse/CommandTable.h"
#include "obse/GameAPI.h"
#include "obse/GameActorValues.h"
#include "obse/GameExtraData.h"
#include "obse/GameForms.h"
#include "obse/GameObjects.h"
#include "obse/GameProcess.h"

#include <cstring>

namespace lc::awareness
{
	namespace
	{
		enum Fn { kSitting, kSleeping, kPackage, kWeaponOut, kSneaking, kInCombat, kDetected, kArmor, kClothing,
		          kSwimming, kFnCount };
		const char* const kFnNames[kFnCount] = {
			"GetSitting", "GetSleeping", "GetCurrentAIPackage", "IsWeaponOut", "IsSneaking", "IsInCombat",
			"GetDetected", "GetArmorRating", "GetClothingValue", "IsSwimming",
		};
		Cmd_Eval g_fn[kFnCount] = {};
		const UInt8 kFormTypeBook = 21;     // kFormType_Book in xOBSE's GameForms.h

		// A condition function's value, or -1 if it can't be read. `arg` is the
		// condition's parameter (a form for GetDetected). No C++ objects: __try.
		double Eval(Fn fn, TESObjectREFR* ref, void* arg = nullptr)
		{
			if (!g_fn[fn] || !ref)
				return -1;
			__try
			{
				double result = 0;
				return g_fn[fn](ref, arg, nullptr, &result) ? result : -1;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return -1;
			}
		}

		// The item the NPC is using for an idle (reading a book, mixing a
		// potion): LowProcess::usedItem (xOBSE GameProcess.h).
		TESForm* UsedItem(TESObjectREFR* ref)
		{
			__try
			{
				if (!ref || ref->typeID != kFormType_ACHR)
					return nullptr;
				Actor* a = reinterpret_cast<Actor*>(ref);
				if (!a->process || a->process->GetProcessLevel() > 3)
					return nullptr;
				return static_cast<LowProcess*>(a->process)->usedItem;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return nullptr;
			}
		}

		// What the NPC has equipped: the forms marked worn in their inventory
		// (as xOBSE's Actor::GetEquippedItems, but into a fixed array: no C++
		// objects inside __try). Returns how many were found.
		int EquippedForms(TESObjectREFR* ref, TESForm** out, int max)
		{
			int count = 0;
			__try
			{
				if (!ref || ref->typeID != kFormType_ACHR)
					return 0;
				ExtraContainerChanges* x = static_cast<ExtraContainerChanges*>(ref->baseExtraList.GetByType(kExtraData_ContainerChanges));
				if (!x || !x->data || !x->data->objList)
					return 0;
				int guard = 0;
				for (tList<ExtraContainerChanges::EntryData>::Iterator e = x->data->objList->Begin(); !e.End() && count < max && guard < 2000; ++e, ++guard)
				{
					if (!*e || !e->extendData || !e->type)
						continue;
					bool worn = false;
					for (tList<ExtraDataList>::Iterator l = e->extendData->Begin(); !l.End() && !worn; ++l)
						worn = *l && (l->HasType(kExtraData_Worn) || l->HasType(kExtraData_WornLeft));
					if (worn)
						out[count++] = e->type;
				}
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
			}
			return count;
		}

		// "bow", "light armor", "shield", "clothing"... ("" = leave it out).
		const char* KindOf(TESForm* f)
		{
			__try
			{
				switch (f->typeID)
				{
				case kFormType_Weapon:
					switch (static_cast<TESObjectWEAP*>(f)->type)
					{
					case TESObjectWEAP::kType_BladeOneHand: return "one-handed blade";
					case TESObjectWEAP::kType_BladeTwoHand: return "two-handed blade";
					case TESObjectWEAP::kType_BluntOneHand: return "one-handed blunt weapon";
					case TESObjectWEAP::kType_BluntTwoHand: return "two-handed blunt weapon";
					case TESObjectWEAP::kType_Staff:        return "staff";
					case TESObjectWEAP::kType_Bow:          return "bow";
					default:                                return "weapon";
					}
				case kFormType_Ammo:
					return "arrows";
				case kFormType_Armor:
				{
					TESObjectARMO* a = static_cast<TESObjectARMO*>(f);
					if (a->bipedModel.partMask & (1 << TESBipedModelForm::kPart_Shield))
						return "shield";
					return a->IsHeavyArmor() ? "heavy armor" : "light armor";
				}
				case kFormType_Clothing:
					return "clothing";
				default:
					return "";     // rings and amulets are clothing; torches, lights etc. left out
				}
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return "";
			}
		}

		// The player's race and sex, as anyone looking at them sees (0.21.6).
		bool RaceAndSex(TESObjectREFR* ref, const char** race, bool* female)
		{
			__try
			{
				TESNPC* base = ref && ref->baseForm ? OBLIVION_CAST(ref->baseForm, TESForm, TESNPC) : nullptr;
				if (!base)
					return false;
				*female = base->actorBaseData.IsFemale();
				*race = base->race.race ? GetFullName(base->race.race) : nullptr;
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		int HealthPercent(Actor* a)
		{
			__try
			{
				const float base = a->GetCalculatedBaseAV(kActorVal_Health);
				return base > 0 ? static_cast<int>(100.0f * a->GetActorValue(kActorVal_Health) / base) : -1;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return -1;
			}
		}
	}

	void Init(const OBSECommandTableInterface* commands)
	{
		int found = 0;
		for (int i = 0; i < kFnCount; ++i)
		{
			const CommandInfo* cmd = commands && commands->GetByName ? commands->GetByName(kFnNames[i]) : nullptr;
			g_fn[i] = cmd ? cmd->eval : nullptr;
			if (g_fn[i])
				++found;
			else
				LC_WARN("Awareness: the game's %s wasn't found", kFnNames[i]);
		}
		LC_MSG("Awareness: %d of %d condition functions found", found, kFnCount);
	}

	lc::json::Value Activity(TESObjectREFR* npc)
	{
		lc::json::Value v;
		v.Set("sitting", Eval(kSitting, npc)).Set("sleeping", Eval(kSleeping, npc)).Set("package", Eval(kPackage, npc))
			.Set("weapon_out", Eval(kWeaponOut, npc));
		if (TESForm* item = UsedItem(npc))
		{
			const char* name = GetFullName(item);
			if (item->typeID == kFormTypeBook)
				v.Set("reading", true);
			if (name && *name && strcmp(name, "<no name>") != 0)
				v.Set("using", name);
		}
		return v;
	}

	lc::json::Value Equipment(TESObjectREFR* npc)
	{
		lc::json::Value out = lc::json::Value::MakeArray();
		const int kMax = 32;
		TESForm* forms[kMax] = {};
		const int n = EquippedForms(npc, forms, kMax);
		for (int i = 0; i < n; ++i)
		{
			const char* kind = KindOf(forms[i]);
			const char* name = GetFullName(forms[i]);
			if (!*kind || !name || !*name || strcmp(name, "<no name>") == 0)
				continue;
			lc::json::Value v;
			v.Set("name", name).Set("kind", kind);
			out.Push(v);
		}
		return out;
	}

	lc::json::Value PlayerAsSeenBy(TESObjectREFR* npc)
	{
		lc::json::Value v;
		PlayerCharacter* player = g_thePlayer ? *g_thePlayer : nullptr;
		if (!player)
			return v;
		v.Set("sees", Eval(kDetected, npc, player))
			.Set("weapon_out", Eval(kWeaponOut, player))
			.Set("sneaking", Eval(kSneaking, player))
			.Set("in_combat", Eval(kInCombat, player))
			.Set("armor_rating", Eval(kArmor, player))
			.Set("clothing_value", Eval(kClothing, player))
			.Set("swimming", Eval(kSwimming, player))
			.Set("health_pct", static_cast<double>(HealthPercent(player)));
		// MVXK21 (Nov 11): NPCs should know everything about the player that
		// anyone seeing them would: race, sex and what they're wearing and carrying.
		const char* race = nullptr;
		bool female = false;
		if (RaceAndSex(player, &race, &female))
		{
			v.Set("sex", female ? "female" : "male");
			if (race && *race && strcmp(race, "<no name>") != 0)
				v.Set("race", race);
		}
		v.Set("equipment", Equipment(player));
		return v;
	}
}
