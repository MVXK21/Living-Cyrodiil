#include "quests.h"
#include "log.h"

#include "obse/PluginAPI.h"
#include "obse/CommandTable.h"
#include "obse/GameAPI.h"
#include "obse/GameData.h"
#include "obse/GameForms.h"
#include "obse/GameObjects.h"

#include <cstring>

namespace lc::quests
{
	namespace
	{
		Cmd_Eval g_stageDone = nullptr;
		const int kMaxQuests = 1500;
		const int kMaxStages = 64;
		const int kMaxGlobals = 1500;
		bool g_loggedOnce = false;

		struct QuestInfo
		{
			char  name[64];
			int   stage;
			bool  completed;
			int   doneCount;
			UInt8 done[kMaxStages];
		};
		struct GlobalInfo
		{
			char  name[64];
			float value;
		};
		QuestInfo  g_quests[kMaxQuests];
		GlobalInfo g_globals[kMaxGlobals];

		void CopyName(char* out, const BSStringT& s)
		{
			out[0] = '\0';
			if (s.m_data && s.m_dataLen > 0 && s.m_dataLen < 64)
			{
				memcpy(out, s.m_data, s.m_dataLen);
				out[s.m_dataLen] = '\0';
			}
		}

		bool StageDone(TESObjectREFR* player, TESQuest* q, UInt8 stage)
		{
			double result = 0;
			return g_stageDone && g_stageDone(player, q, reinterpret_cast<void*>(static_cast<UINT_PTR>(stage)), &result)
				&& result != 0;
		}

		// No C++ objects: runs inside __try. Returns the counts read.
		void Gather(int* questCount, int* globalCount)
		{
			*questCount = *globalCount = 0;
			__try
			{
				DataHandler* dh = g_dataHandler ? *g_dataHandler : nullptr;
				TESObjectREFR* player = g_thePlayer ? *g_thePlayer : nullptr;
				if (!dh || !player)
					return;
				for (tList<TESQuest>::Iterator it = dh->quests.Begin(); !it.End() && *questCount < kMaxQuests; ++it)
				{
					TESQuest* q = it.Get();
					if (!q || (q->stageIndex == 0 && !q->IsCompleted()))
						continue;
					QuestInfo& info = g_quests[*questCount];
					CopyName(info.name, q->editorName);
					if (!info.name[0])
						continue;
					info.stage = q->stageIndex;
					info.completed = q->IsCompleted();
					info.doneCount = 0;
					for (tList<TESQuest::StageEntry>::Iterator s = q->stageList.Begin(); !s.End(); ++s)
					{
						TESQuest::StageEntry* e = s.Get();
						if (e && info.doneCount < kMaxStages && StageDone(player, q, e->index))
							info.done[info.doneCount++] = e->index;
					}
					++*questCount;
				}
				for (tList<TESGlobal>::Iterator it = dh->globals.Begin(); !it.End() && *globalCount < kMaxGlobals; ++it)
				{
					TESGlobal* g = it.Get();
					if (!g || g->data == 0)
						continue;
					GlobalInfo& info = g_globals[*globalCount];
					CopyName(info.name, g->name);
					if (!info.name[0])
						continue;
					info.value = g->data;
					++*globalCount;
				}
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Quests: crash avoided while reading quest stages (exception %08X); reporting what was read",
					GetExceptionCode());
			}
		}
	}

	void Init(const OBSECommandTableInterface* commands)
	{
		const CommandInfo* cmd = commands && commands->GetByName ? commands->GetByName("GetStageDone") : nullptr;
		g_stageDone = cmd ? cmd->eval : nullptr;
		if (g_stageDone)
			LC_MSG("Quests: found the game's GetStageDone");
		else
			LC_WARN("Quests: the game's GetStageDone wasn't found; only current stages will be reported");
	}

	lc::json::Value Report()
	{
		int questCount = 0, globalCount = 0;
		Gather(&questCount, &globalCount);
		lc::json::Value quests, globals, out;
		for (int i = 0; i < questCount; ++i)
		{
			const QuestInfo& q = g_quests[i];
			lc::json::Value done = lc::json::Value::MakeArray();
			for (int d = 0; d < q.doneCount; ++d)
				done.Push(static_cast<double>(q.done[d]));
			lc::json::Value v;
			v.Set("stage", static_cast<double>(q.stage)).Set("done", done);
			if (q.completed)
				v.Set("completed", true);
			quests.Set(q.name, v);
		}
		for (int i = 0; i < globalCount; ++i)
			globals.Set(g_globals[i].name, static_cast<double>(g_globals[i].value));
		if (!g_loggedOnce)
		{
			LC_MSG("Quests: %d started quests and %d set globals reported", questCount, globalCount);
			g_loggedOnce = true;
		}
		out.Set("quests", quests).Set("globals", globals);
		return out;
	}
}
