#include "playthrough.h"
#include "log.h"

#include "obse/GameAPI.h"
#include "obse/GameForms.h"
#include "obse/GameObjects.h"

#include <windows.h>
#include <bcrypt.h>
#include <shlobj.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

#pragma comment(lib, "bcrypt.lib")

namespace lc::playthrough
{
	namespace
	{
		std::string g_id;
		long long   g_seq = kUnknownSeq;
		bool        g_fromFile = false;

		std::string NewId()
		{
			unsigned char bytes[16] = {};
			BCryptGenRandom(nullptr, bytes, sizeof(bytes), BCRYPT_USE_SYSTEM_PREFERRED_RNG);
			char out[40] = "pt-";
			for (int i = 0; i < 16; ++i)
				sprintf_s(out + 3 + i * 2, sizeof(out) - 3 - i * 2, "%02x", bytes[i]);
			return out;
		}

		// For a save with no record: the same character gives the same id. No
		// C++ objects: the reads run in __try.
		bool ReadPlayer(char* name, size_t size, UInt32* raceId, bool* female)
		{
			__try
			{
				PlayerCharacter* p = g_thePlayer ? *g_thePlayer : nullptr;
				TESNPC* npc = p && p->baseForm ? OBLIVION_CAST(p->baseForm, TESForm, TESNPC) : nullptr;
				if (!npc)
					return false;
				const char* n = GetFullName(npc);
				strncpy_s(name, size, n ? n : "", _TRUNCATE);
				*raceId = npc->race.race ? npc->race.race->refID & 0x00FFFFFF : 0;
				*female = npc->actorBaseData.IsFemale();
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		std::string IdFromCharacter()
		{
			char name[256] = {};
			UInt32 race = 0;
			bool female = false;
			if (!ReadPlayer(name, sizeof(name), &race, &female) || !name[0])
				return NewId();
			unsigned long long h = 1469598103934665603ULL;       // FNV-1a, 64 bits
			auto mix = [&h](const void* data, size_t n) {
				const unsigned char* b = static_cast<const unsigned char*>(data);
				for (size_t i = 0; i < n; ++i) { h ^= b[i]; h *= 1099511628211ULL; }
			};
			mix(name, strlen(name));
			mix(&race, sizeof(race));
			mix(&female, sizeof(female));
			char out[40];
			sprintf_s(out, "char-%016llx", h);
			LC_MSG("Playthrough: this save has no record yet; using an id made from the character (%s)", name);
			return out;
		}

		std::string SavesDir()
		{
			char docs[MAX_PATH] = {};
			if (FAILED(SHGetFolderPathA(nullptr, CSIDL_PERSONAL, nullptr, 0, docs)))
				return {};
			std::string dir = std::string(docs) + "\\My Games\\Oblivion\\LivingCyrodiil";
			CreateDirectoryA(dir.c_str(), nullptr);
			dir += "\\saves";
			CreateDirectoryA(dir.c_str(), nullptr);
			return dir;
		}

		// "C:\...\Save 17 - Mathias.ess" or "Save 17 - Mathias.ess" -> "Save 17 - Mathias".
		std::string SaveStem(const std::string& pathOrName)
		{
			std::string s = pathOrName;
			if (size_t slash = s.find_last_of("\\/"); slash != std::string::npos)
				s = s.substr(slash + 1);
			if (s.size() > 4 && _stricmp(s.c_str() + s.size() - 4, ".ess") == 0)
				s.resize(s.size() - 4);
			return s;
		}

		std::string RecordPath(const std::string& pathOrName)
		{
			const std::string dir = SavesDir();
			const std::string stem = SaveStem(pathOrName);
			return dir.empty() || stem.empty() ? std::string() : dir + "\\" + stem + ".txt";
		}
	}

	void BeforeLoad()
	{
		g_id.clear();
		g_seq = kUnknownSeq;
		g_fromFile = false;
	}

	void OnLoad(const std::string& saveName)
	{
		BeforeLoad();
		const std::string path = RecordPath(saveName);
		FILE* f = nullptr;
		if (path.empty() || fopen_s(&f, path.c_str(), "r") != 0 || !f)
		{
			LC_MSG("Playthrough: no record for this save (%s)", SaveStem(saveName).c_str());
			return;
		}
		// Two lines: "id <id>" and "seq <number>".
		char line[256];
		while (fgets(line, sizeof(line), f))
		{
			line[strcspn(line, "\r\n")] = 0;
			if (strncmp(line, "id ", 3) == 0)
				g_id = line + 3;
			else if (strncmp(line, "seq ", 4) == 0)
				g_seq = _strtoi64(line + 4, nullptr, 10);
		}
		fclose(f);
		g_fromFile = !g_id.empty();
		if (!g_fromFile)
			g_seq = kUnknownSeq;
		LC_MSG("Playthrough: %s", g_fromFile ? ("loaded " + g_id + ", memory " + std::to_string(g_seq)).c_str()
			: "the save's record was unreadable");
	}

	void AfterLoad()
	{
		if (g_id.empty())
		{
			g_id = IdFromCharacter();
			g_seq = kUnknownSeq;
		}
	}

	void OnSave(const std::string& savePath)
	{
		if (g_id.empty())
			return;                    // nobody talked yet in a new game: nothing to remember
		const std::string path = RecordPath(savePath);
		FILE* f = nullptr;
		if (path.empty() || fopen_s(&f, path.c_str(), "w") != 0 || !f)
		{
			LC_WARN("Playthrough: couldn't write the record for %s", SaveStem(savePath).c_str());
			return;
		}
		fprintf(f, "id %s\nseq %lld\n", g_id.c_str(), g_seq);
		fclose(f);
		LC_MSG("Playthrough: saved %s, memory %lld, for %s", g_id.c_str(), g_seq, SaveStem(savePath).c_str());
	}

	void Reset()
	{
		BeforeLoad();
	}

	bool Ensure()
	{
		if (!g_id.empty())
			return false;
		g_id = NewId();
		g_seq = 0;
		LC_MSG("Playthrough: new id %s", g_id.c_str());
		return true;
	}

	const std::string& Id() { return g_id; }
	long long Seq() { return g_seq; }

	void SetSeq(const std::string& id, long long seq)
	{
		if (id == g_id && seq > g_seq)
			g_seq = seq;
	}
}
