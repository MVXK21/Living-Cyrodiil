#include "esp_forms.h"
#include "log.h"

#include "obse/GameAPI.h"
#include "obse/GameData.h"
#include "obse/GameForms.h"

#include <map>

namespace lc::esp
{
	namespace
	{
		// No C++ objects: runs inside __try.
		TESForm* LookUp(unsigned localId, UInt32* formIdOut)
		{
			__try
			{
				DataHandler* dh = g_dataHandler ? *g_dataHandler : nullptr;
				if (!dh)
					return nullptr;
				const UInt8 index = dh->GetModIndex(kPluginFile);
				if (index == 0xFF)
					return nullptr;
				*formIdOut = (static_cast<UInt32>(index) << 24) | localId;
				return LookupFormByID(*formIdOut);
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return nullptr;
			}
		}

		bool WriteAndReadBack(TESGlobal* g, float value, float* readBack)
		{
			__try { g->data = value; *readBack = g->data; return true; }
			__except (EXCEPTION_EXECUTE_HANDLER) { return false; }
		}

		std::map<unsigned, TESForm*> g_cache;
	}

	TESForm* Find(unsigned localId, const char* what)
	{
		auto it = g_cache.find(localId);
		if (it != g_cache.end())
			return it->second;
		UInt32 id = 0;
		TESForm* form = LookUp(localId, &id);
		if (form)
			LC_MSG("Found %s (%08X) from %s", what, id, kPluginFile);
		else
			LC_ERR("Couldn't find %s (local id %06X) from %s. Is the .esp active in the load order?",
				what, localId, kPluginFile);
		g_cache[localId] = form;
		return form;
	}

	TESGlobal* FindGlobal(unsigned localId, const char* what)
	{
		TESForm* form = Find(localId, what);
		return form && form->typeID == kFormType_Global ? static_cast<TESGlobal*>(form) : nullptr;
	}

	bool SetGlobal(TESGlobal* global, float value)
	{
		float now = -1;
		return global && WriteAndReadBack(global, value, &now) && now == value;
	}
}
