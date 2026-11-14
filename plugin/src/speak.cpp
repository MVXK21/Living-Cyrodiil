#include "speak.h"
#include "log.h"
#include "notify.h"
#include "esp_forms.h"
#include "hold.h"
#include "chatter.h"

#include "obse/PluginAPI.h"
#include "obse/GameAPI.h"
#include "obse/GameData.h"
#include "obse/GameForms.h"
#include "obse/GameMenus.h"
#include "obse/GameObjects.h"
#include "obse/GameTiles.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace lc::speak
{
	namespace
	{
		OBSEConsoleInterface* g_console = nullptr;

		const ULONGLONG kExtraWatchMs = 3000;   // keep watching this long after the audio ends
		const ULONGLONG kFallbackMs = 3000;     // no subtitle by then: show a notification
		const int       kMaxDepth = 16;
		const int       kMaxTiles = 800;

		std::string g_dataDir;                  // the game's Data folder, ending in a backslash

		// Pending reply being shown
		bool        g_active = false;
		ULONGLONG   g_started = 0;
		ULONGLONG   g_until = 0;
		char        g_chunks[kMaxChunks][1024] = {};   // reply chunks in Windows-1252, ready for the tile
		int         g_chunkCount = 0;
		std::string g_utf8Text;                // whole reply, for the fallback notification
		bool        g_fallbackDone = false;
		bool        g_dumped = false;
		bool        g_replacedOnce = false;
		int         g_replacements = 0;

		// ---- voiced replies (Phase 3 step 3) ------------------------------------
		// The companion sends each part's speech as an MP3 (reply_audio). Each
		// part waits for its audio before it's said, up to kAudioWaitMs, then
		// goes ahead silently. The MP3 is copied over the part's voice files.
		const ULONGLONG kAudioWaitMs = 10000;
		const double    kVoiceTailSeconds = 0.3;   // subtitle stays up a moment after the voice
		enum class Audio { Waiting, Ready, Failed };
		bool         g_voiced = false;
		int          g_turn = 0;
		Audio        g_audio[kMaxChunks] = {};
		std::wstring g_audioPath[kMaxChunks];
		std::wstring g_lipPath[kMaxChunks];          // Phase 4: the part's .lip ("" = none)
		double       g_audioSeconds[kMaxChunks] = {};
		std::vector<std::string> g_partFiles[kMaxChunks];   // where each part's voice file was written
		int          g_waitingPart = -1;            // part waiting for its audio (-1 = none)
		ULONGLONG    g_waitSince = 0;

		// ---- filler sound, then the real reply (step 4, MVXK21, Nov 6) -------------
		// When the reply is slow the companion first sends a short filler ("Hmm.")
		// as a reply of its own. If the real reply arrives while the filler plays,
		// it waits here, with any of its audio that comes meanwhile, and starts as
		// soon as the filler is done.
		bool g_isFiller = false;
		struct PendingReply
		{
			bool                     has = false;
			std::string              refId;
			std::vector<std::string> chunks;
			bool                     voiced = false;
			int                      turn = 0;
			bool                     streaming = false;    // chunks is part 1 only...
			bool                     partsKnown = false;   // ...until reply_parts came
			Audio                    audio[kMaxChunks] = {};
			std::wstring             path[kMaxChunks];
			std::wstring             lip[kMaxChunks];
			double                   seconds[kMaxChunks] = {};
		};
		PendingReply g_pending;

		// ---- streamed replies (step 4b) --------------------------------------
		// Part 1 comes alone, as soon as the companion has it; reply_parts then
		// gives every part. If the list hasn't come within kAudioWaitMs of
		// part 1 ending, the reply ends there.
		bool      g_partsKnown = true;
		ULONGLONG g_partsWaitSince = 0;      // part 1 over, waiting for the list (0 = not waiting)

		// Up to kMaxChunks non-empty chunks; any extra ones are merged into the last.
		std::vector<std::string> FitChunks(const std::vector<std::string>& in)
		{
			std::vector<std::string> out;
			for (const std::string& c : in)
			{
				if (c.empty())
					continue;
				if (static_cast<int>(out.size()) < kMaxChunks)
					out.push_back(c);
				else
					out.back() += " " + c;
			}
			return out;
		}

		// ---- General Subtitles, on only while a reply shows -------------------
		// Many players turn off subtitles for NPCs outside the dialogue window
		// (bGeneralSubtitles=0, as in MVXK21's setup), and SayTo's "force
		// subtitle" flag doesn't override it. So the plugin switches the
		// setting on in memory for the reply and restores it afterwards. The
		// INI file is never written.

		SettingInfo* g_generalSubs = nullptr;
		bool         g_subsLookedUp = false;
		bool         g_restoreSubs = false;

		// No C++ objects: runs inside __try.
		SettingInfo* FindIniSetting(const char* name)
		{
			__try
			{
				IniSettingCollection* s = IniSettingCollection::GetSingleton();
				if (!s)
					return nullptr;
				const size_t len = strlen(name);
				for (INISettingEntry* e = &s->settingsList; e; e = e->next)
				{
					SettingInfo* d = e->data;
					if (d && d->name && _strnicmp(d->name, name, len) == 0 && (d->name[len] == ':' || d->name[len] == '\0'))
						return d;
				}
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Crash avoided while looking up INI setting %s (exception %08X)", name, GetExceptionCode());
			}
			return nullptr;
		}

		bool SetBoolSetting(SettingInfo* s, bool value)
		{
			__try { s->b = value; return true; }
			__except (EXCEPTION_EXECUTE_HANDLER) { return false; }
		}

		bool ReadBoolSetting(SettingInfo* s, bool* value)
		{
			__try { *value = s->b; return true; }
			__except (EXCEPTION_EXECUTE_HANDLER) { return false; }
		}

		void SubtitlesOnForReply()
		{
			if (!g_subsLookedUp)
			{
				g_subsLookedUp = true;
				g_generalSubs = FindIniSetting("bGeneralSubtitles");
				if (!g_generalSubs)
					LC_WARN("Couldn't find the bGeneralSubtitles setting; replies need General Subtitles on");
			}
			bool on = true;
			if (!g_generalSubs || !ReadBoolSetting(g_generalSubs, &on) || on)
				return;
			if (SetBoolSetting(g_generalSubs, true))
			{
				g_restoreSubs = true;
				LC_MSG("General Subtitles were off: turned on for this reply");
			}
		}

		void RestoreSubtitles()
		{
			if (!g_restoreSubs)
				return;
			g_restoreSubs = false;
			if (g_generalSubs && SetBoolSetting(g_generalSubs, false))
				LC_MSG("General Subtitles turned back off");
		}

		// ---- LCPart global (step 6d) ----------------------------------------
		// Set directly in memory: "set LCChunks to N" through the console
		// interface didn't take in the 0.6.0 test.

		bool SetPartGlobal(int part)
		{
			return lc::esp::SetGlobal(lc::esp::FindGlobal(lc::esp::kPartGlobal, kPartGlobal), static_cast<float>(part));
		}

		// ---- silent voice file (step 6c) ------------------------------------

		// Reading time for the subtitle: about 15 characters a second, plus a
		// moment to notice it. Long replies are split across subtitles in 6d.
		double ReadingSeconds(size_t chars)
		{
			return std::clamp(1.0 + chars / 15.0, 2.5, 25.0);
		}

		// Silent MP3: MPEG-1 Layer III, 44.1 kHz, mono, 64 kbps, constant
		// bitrate. Each frame is a 4-byte header followed by zeros (empty side
		// info and no audio data), which decodes as silence. Padding bytes are
		// spread out like a real encoder's, so the length the game reads from
		// the bitrate matches the frame count.
		std::vector<unsigned char> SilentMp3(double seconds)
		{
			const int sampleRate = 44100, bitrate = 64000, samplesPerFrame = 1152;
			const int frames = static_cast<int>(seconds * sampleRate / samplesPerFrame) + 1;
			std::vector<unsigned char> out;
			out.reserve(static_cast<size_t>(frames) * 210);
			long long rest = 0;                 // padding accumulator, in 1/sampleRate bytes
			for (int i = 0; i < frames; ++i)
			{
				const long long exact = 144LL * bitrate;     // frame bytes * sampleRate
				int size = static_cast<int>(exact / sampleRate);
				rest += exact % sampleRate;
				bool pad = false;
				if (rest >= sampleRate) { rest -= sampleRate; pad = true; ++size; }
				out.push_back(0xFF);
				out.push_back(0xFB);                // MPEG-1, Layer III, no CRC
				out.push_back(static_cast<unsigned char>(0x50 | (pad ? 0x02 : 0x00)));   // 64 kbps, 44.1 kHz
				out.push_back(0xC0);                // mono
				out.insert(out.end(), static_cast<size_t>(size - 4), 0);
			}
			return out;
		}

		bool MakeDirs(const std::string& dir)
		{
			for (size_t i = g_dataDir.size(); i <= dir.size(); ++i)
			{
				if (i == dir.size() || dir[i] == '\\')
				{
					std::string part = dir.substr(0, i);
					if (!CreateDirectoryA(part.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS)
					{
						LC_WARN("Voice file: couldn't create folder %s (error %lu)", part.c_str(), GetLastError());
						return false;
					}
				}
			}
			return true;
		}

		bool WriteFileBytes(const std::string& path, const std::vector<unsigned char>& bytes)
		{
			HANDLE h = CreateFileA(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
				CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
			if (h == INVALID_HANDLE_VALUE)
			{
				LC_WARN("Voice file: couldn't write %s (error %lu)", path.c_str(), GetLastError());
				return false;
			}
			DWORD written = 0;
			BOOL ok = WriteFile(h, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr);
			CloseHandle(h);
			return ok && written == bytes.size();
		}

		// Reads the NPC's race name, voice race name and sex. No C++ objects:
		// runs inside __try. Names are copied into the caller's buffers.
		bool ReadVoiceInfo(TESNPC* npc, char* raceName, char* voiceRaceName, size_t len, bool* female)
		{
			__try
			{
				*female = npc->actorBaseData.IsFemale();
				TESRace* race = npc->race.race;
				if (!race)
					return false;
				const char* n = race->fullName.name.m_data;
				strncpy_s(raceName, len, n ? n : "", _TRUNCATE);
				TESRace* voice = race->voiceRaces[*female ? 1 : 0];
				const char* v = voice ? voice->fullName.name.m_data : nullptr;
				strncpy_s(voiceRaceName, len, v ? v : "", _TRUNCATE);
				return raceName[0] != '\0';
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Crash avoided while reading the NPC's race (exception %08X)", GetExceptionCode());
				return false;
			}
		}

		// Writes the silent voice files where the game looks for the line's
		// responses:
		//   Data\Sound\Voice\LivingCyrodiil.esp\<race>\<M|F>\LCQuest_LCReply_<line id>_<r>.mp3
		// one per chunk r, sized to that chunk. Written for both the NPC's race
		// and its voice race (modded races often borrow a vanilla race's
		// voices; Tsrava's "Khajiit Cathay" uses Argonian\F), since either may
		// be used. Writing at run time works: the game checks for the file each
		// time a line is said (seen in the 0.5.4 voice-file log).
		void WriteVoiceFiles(TESNPC* npc, int firstPart, int lastPart, const double* seconds)
		{
			char raceName[128] = {}, voiceName[128] = {};
			bool female = false;
			if (!ReadVoiceInfo(npc, raceName, voiceName, sizeof(raceName), &female))
			{
				LC_WARN("Voice file: couldn't read the NPC's race; the line may not play");
				return;
			}
			std::vector<std::string> races{ raceName };
			if (voiceName[0] && _stricmp(voiceName, raceName) != 0)
				races.push_back(voiceName);
			for (int r = firstPart; r <= lastPart; ++r)
			{
				g_partFiles[r - 1].clear();
				const char* lineId = kLineFileIds[r - 1];     // part r's own line
				const std::vector<unsigned char> mp3 = SilentMp3(seconds[r - 1]);
				for (const std::string& race : races)
				{
					std::string dir = g_dataDir + "Sound\\Voice\\" + kPluginFile + "\\" + race + (female ? "\\F" : "\\M");
					if (!MakeDirs(dir))
						continue;
					char name[96];
					sprintf_s(name, "\\%s_%s_%s_1.mp3", kQuestEditorId, kTopicEditorId, lineId);
					std::string file = dir + name;
					// No lip file from an earlier reply may play with this one.
					const std::string lip = file.substr(0, file.size() - 4) + ".lip";
					if (DeleteFileA(lip.c_str()))
						LC_MSG("Lip file: removed the last reply's %s", lip.c_str());
					if (WriteFileBytes(file, mp3))
					{
						g_partFiles[r - 1].push_back(file);
						LC_MSG("Voice file: wrote %.1fs of silence (%u bytes) to %s", seconds[r - 1],
							static_cast<unsigned>(mp3.size()), file.c_str());
					}
				}
			}
		}

		std::wstring Widen(const std::string& s, UINT codePage)
		{
			int n = MultiByteToWideChar(codePage, 0, s.c_str(), -1, nullptr, 0);
			if (n <= 0) return {};
			std::wstring w(n, L'\0');
			MultiByteToWideChar(codePage, 0, s.c_str(), -1, w.data(), n);
			if (!w.empty() && w.back() == L'\0') w.pop_back();
			return w;
		}

		// Copies part `index`'s speech over its voice files (both race folders),
		// replacing the silence. The copy runs in the game process, so under MO2
		// it lands in the virtual Data folder like the silent files. Returns
		// true if at least one copy worked.
		bool CopyVoiceAudio(int index)
		{
			bool any = false;
			for (const std::string& file : g_partFiles[index])
			{
				if (CopyFileW(g_audioPath[index].c_str(), Widen(file, CP_ACP).c_str(), FALSE))
				{
					any = true;
					LC_MSG("Voice file: part %d's speech (%.1fs) copied to %s", index + 1, g_audioSeconds[index], file.c_str());
				}
				else
					LC_WARN("Voice file: couldn't copy part %d's speech to %s (error %lu)", index + 1, file.c_str(), GetLastError());
				// Phase 4: the part's lip file, next to the voice file with the same name.
				if (!g_lipPath[index].empty())
				{
					const std::string lip = file.substr(0, file.size() - 4) + ".lip";
					if (CopyFileW(g_lipPath[index].c_str(), Widen(lip, CP_ACP).c_str(), FALSE))
						LC_MSG("Lip file: part %d's lips copied to %s", index + 1, lip.c_str());
					else
						LC_WARN("Lip file: couldn't copy part %d's lips to %s (error %lu)", index + 1, lip.c_str(), GetLastError());
				}
			}
			return any;
		}

		void FindDataDir()
		{
			// Our DLL sits in Data\OBSE\Plugins (under MO2, the virtual Data folder).
			HMODULE self = nullptr;
			char path[MAX_PATH] = {};
			if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
					reinterpret_cast<LPCSTR>(&FindDataDir), &self) && GetModuleFileNameA(self, path, MAX_PATH))
			{
				std::string p(path);
				for (int up = 0; up < 3; ++up)
				{
					size_t slash = p.find_last_of("\\/");
					if (slash == std::string::npos) { p.clear(); break; }
					p.resize(slash);
				}
				size_t slash = p.find_last_of("\\/");
				if (!p.empty() && _stricmp(p.c_str() + (slash == std::string::npos ? 0 : slash + 1), "Data") == 0)
				{
					g_dataDir = p + "\\";
					LC_MSG("Data folder: %s", g_dataDir.c_str());
					return;
				}
			}
			char cwd[MAX_PATH] = {};
			GetCurrentDirectoryA(MAX_PATH, cwd);
			g_dataDir = std::string(cwd) + "\\Data\\";
			LC_WARN("Data folder: couldn't work it out from the DLL path (%s); using %s", path, g_dataDir.c_str());
		}

		std::string ToGameText(const std::string& utf8)
		{
			std::string out;
			int wlen = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
			if (wlen <= 0) return out;
			std::wstring wide(wlen, L'\0');
			MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, wide.data(), wlen);
			int alen = WideCharToMultiByte(1252, 0, wide.c_str(), -1, nullptr, 0, "?", nullptr);
			if (alen <= 0) return out;
			out.resize(alen);
			WideCharToMultiByte(1252, 0, wide.c_str(), -1, out.data(), alen, "?", nullptr);
			if (!out.empty() && out.back() == '\0') out.pop_back();
			for (char& c : out)
				if (static_cast<unsigned char>(c) < 0x20) c = ' ';
			return out;
		}

		// ---- subtitle menu walk (no C++ objects here: called inside __try) -----

		const char* TileString(Tile* t)
		{
			const char* s = nullptr;
			if (t && t->GetStringValue(kTileValue_string, &s) && s && *s)
				return s;
			return nullptr;
		}

		void DumpTiles(Tile* t, int depth, int* count)
		{
			if (!t || depth > kMaxDepth || ++(*count) > kMaxTiles) return;
			const char* name = t->name.m_data ? t->name.m_data : "?";
			const char* s = TileString(t);
			LC_MSG("  subtitle menu %*s%s%s%s%s", depth * 2, "", name, s ? " = \"" : "", s ? s : "", s ? "\"" : "");
			for (auto* n = t->childList.start; n; n = n->next)
				DumpTiles(n->data, depth + 1, count);
		}

		// The subtitle tile we last wrote into, and what it last showed, so
		// every change of subtitle text during a reply is logged.
		Tile* g_textTile = nullptr;
		char  g_lastSeen[1024] = {};
		int   g_changesLogged = 0;

		// The part being said (0-based), and whether its text has been put up
		// and has since cleared (the part is over).
		int   g_part = 0;
		bool  g_partShown = false;
		bool  g_subtitleBlank = false;

		void LogSubtitleChange()
		{
			const char* s = g_textTile ? TileString(g_textTile) : nullptr;
			if (!s)
				s = "";
			bool blank = true;
			for (const char* p = s; *p; ++p)
				if (*p != ' ') { blank = false; break; }
			g_subtitleBlank = blank;
			if (strcmp(s, g_lastSeen) == 0 || g_changesLogged >= 40)
				return;
			strncpy_s(g_lastSeen, s, _TRUNCATE);
			++g_changesLogged;
			LC_MSG("Subtitle now shows (%llu ms in): \"%s\"",
				static_cast<unsigned long long>(GetTickCount64() - g_started), s);
		}

		// While a part is up, keeps our text in the subtitle tile: other NPCs'
		// lines (greetings, idle chatter) land in the same tile and would
		// otherwise replace or clear it. No C++ objects: runs inside __try.
		int g_overrides = 0;

		bool KeepOurSubtitle()
		{
			__try
			{
				if (!g_textTile)
					return true;
				const char* s = TileString(g_textTile);
				const char* ours = g_chunks[g_part];
				if (s && strcmp(s, ours) == 0)
					return true;
				if (g_overrides < 20)
					LC_MSG("Kept our subtitle over: \"%s\"", s ? s : "");
				++g_overrides;
				g_textTile->UpdateString(kTileValue_string, ours);
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Crash avoided while keeping the subtitle (exception %08X)", GetExceptionCode());
				return false;
			}
		}

		// Blanks the tile while it shows the current part, until its voice starts.
		void HideOurSubtitle()
		{
			__try
			{
				const char* s = g_textTile ? TileString(g_textTile) : nullptr;
				if (s && strcmp(s, g_chunks[g_part]) == 0)
					g_textTile->UpdateString(kTileValue_string, " ");
			}
			__except (EXCEPTION_EXECUTE_HANDLER) {}
		}

		// After the last part: clear the tile if it still shows our text, so a
		// subtitle we kept up doesn't linger.
		void ClearOurSubtitle()
		{
			__try
			{
				const char* s = g_textTile ? TileString(g_textTile) : nullptr;
				if (s && g_chunkCount > 0 && strcmp(s, g_chunks[g_chunkCount - 1]) == 0)
					g_textTile->UpdateString(kTileValue_string, " ");
			}
			__except (EXCEPTION_EXECUTE_HANDLER) {}
		}

		// Clears the tile if it shows any part of our reply (or a placeholder).
		void ClearAnyOfOurText()
		{
			__try
			{
				const char* s = g_textTile ? TileString(g_textTile) : nullptr;
				if (!s)
					return;
				bool ours = strstr(s, kPlaceholderPrefix) != nullptr;
				for (int i = 0; i < g_chunkCount && !ours; ++i)
					ours = strcmp(s, g_chunks[i]) == 0;
				if (ours)
					g_textTile->UpdateString(kTileValue_string, " ");
			}
			__except (EXCEPTION_EXECUTE_HANDLER) {}
		}

		// True while the speaker is in the same cell as the player (or, outdoors,
		// within a few cells). No C++ objects: runs inside __try.
		bool SpeakerStillNear(TESObjectREFR* npc)
		{
			__try
			{
				TESObjectREFR* player = g_thePlayer ? *g_thePlayer : nullptr;
				if (!player || !npc || !npc->parentCell || !player->parentCell)
					return false;
				if (npc->parentCell == player->parentCell)
					return true;
				if (npc->parentCell->IsInterior() || player->parentCell->IsInterior()
					|| npc->parentCell->worldSpace != player->parentCell->worldSpace)
					return false;                         // one went through a door or a city gate
				const float dx = player->posX - npc->posX, dy = player->posY - npc->posY;
				return dx * dx + dy * dy < 8000.0f * 8000.0f;   // outdoors: two cells or so
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		void ReplaceIn(Tile* t, int depth, int* count, int* replaced)
		{
			if (!t || depth > kMaxDepth || ++(*count) > kMaxTiles) return;
			const char* s = TileString(t);
			const char* hit = s ? strstr(s, kPlaceholderPrefix) : nullptr;
			if (hit)
			{
				// "(Living Cyrodiil placeholder <r>)" -> chunk r
				const int r = atoi(hit + strlen(kPlaceholderPrefix));
				const int index = (r >= 1 && r <= g_chunkCount) ? r - 1 : 0;
				t->UpdateString(kTileValue_string, g_chunks[index]);
				g_textTile = t;
				if (index == g_part)
					g_partShown = true;
				++(*replaced);
				LC_MSG("Subtitle replaced in tile '%s' with chunk %d of %d", t->name.m_data ? t->name.m_data : "?",
					index + 1, g_chunkCount);
			}
			for (auto* n = t->childList.start; n; n = n->next)
				ReplaceIn(n->data, depth + 1, count, replaced);
		}

		bool AnyText(Tile* t, int depth, int* count)
		{
			if (!t || depth > kMaxDepth || ++(*count) > kMaxTiles) return false;
			if (TileString(t)) return true;
			for (auto* n = t->childList.start; n; n = n->next)
				if (AnyText(n->data, depth + 1, count)) return true;
			return false;
		}

		Tile* SubtitleRoot()
		{
			Menu* menu = GetMenuByType(kMenuType_HUDSubtitle);
			return menu ? reinterpret_cast<Tile*>(menu->tile) : nullptr;
		}

		// Returns false if it crashed (logged); the caller then stops watching.
		bool GuardedTick()
		{
			__try
			{
				Tile* root = SubtitleRoot();
				if (!root)
					return true;
				int count = 0;
				if (!g_dumped && AnyText(root, 0, &count))
				{
					g_dumped = true;
					LC_MSG("Subtitle menu while a line plays (one-time map, for diagnosis):");
					count = 0;
					DumpTiles(root, 0, &count);
				}
				count = 0;
				int replaced = 0;
				ReplaceIn(root, 0, &count, &replaced);
				if (replaced)
				{
					g_replacedOnce = true;
					g_replacements += replaced;
				}
				LogSubtitleChange();
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Crash avoided while updating the subtitle (exception %08X). Please report this with the log.",
					GetExceptionCode());
				return false;
			}
		}

		TESObjectREFR* FindRef(const std::string& refIdHex)
		{
			char* end = nullptr;
			unsigned long id = strtoul(refIdHex.c_str(), &end, 16);
			if (!end || *end || id == 0)
				return nullptr;
			TESForm* form = LookupFormByID(id);
			return form ? OBLIVION_CAST(form, TESForm, TESObjectREFR) : nullptr;
		}
	}

	void Init(void* consoleInterface)
	{
		g_console = static_cast<OBSEConsoleInterface*>(consoleInterface);
		FindDataDir();
	}

	void Stop()
	{
		if (g_active)
			LC_MSG("Reply display stopped (game state change)");
		g_active = false;
		g_waitingPart = -1;
		g_pending = PendingReply();
		RestoreSubtitles();
	}

	namespace
	{
		const ULONGLONG kPartGapMs = 250;        // pause after a part's subtitle clears before the next
		const ULONGLONG kPartOverrunMs = 1500;   // a part that outlasts its audio by this much is over

		std::string g_refId;                     // who is speaking
		double      g_seconds[kMaxChunks] = {};  // each part's audio length
		ULONGLONG   g_partStarted = 0;
		ULONGLONG   g_blankSince = 0;            // when the current part's text first showed (0 = not yet)
		// Radiant chatter (Nov 10 playtest): SayTo does nothing while the NPC is
		// speaking a chatter line. Such a part waits for the line to end, then
		// starts at once; a part whose subtitle didn't appear is tried again.
		const ULONGLONG kChatterWaitMs = 8000;   // longest wait for a chatter line to end
		// The subtitle follows the voice (Nov 10 playtest: after chatter the text
		// came well before the audio). A part's text stays hidden until the game
		// says the NPC is speaking, and its time is counted from then.
		const ULONGLONG kVoiceGraceMs = 200;     // don't hide the text for a delay shorter than this
		const ULONGLONG kVoiceStartWaitMs = 4000; // no sign of the voice by then: show the text anyway
		bool        g_voiceSeen = false;

		// True if `ref` or their chat partner is speaking a line now; `why` says who.
		bool SomeoneSpeaking(TESObjectREFR* ref, std::string* why)
		{
			// With their AI off (hold walk step 4) IsTalking stays "yes" for good,
			// and every part waited 8 s (Nov 12 playtest, No-Coins Draninus). Their
			// earlier lines can only be ours, which end on our own clock.
			const bool aiOff = lc::hold::AiOff() && lc::hold::Held() == g_refId;
			if (!aiOff && lc::chatter::IsTalking(ref) == 1)
			{
				if (why)
					*why = "they are speaking another line";
				return true;
			}
			if (TESObjectREFR* other = lc::chatter::PartnerTalking(ref))
			{
				if (why)
				{
					char buf[96];
					sprintf_s(buf, "their chat partner %08X is speaking", other->refID);
					*why = buf;
				}
				return true;
			}
			return false;
		}
		const int       kMaxChatterRetries = 2;
		int         g_chatterWaitPart = -1;      // part waiting for a chatter line to end (-1 = none)
		ULONGLONG   g_chatterWaitSince = 0;
		int         g_chatterRetries = 0;

		void EndReply(const char* why)
		{
			// However the reply ends, don't leave our text on screen: when the
			// player walked out mid-reply it stayed up until the next conversation
			// (Nov 5, 2026 test).
			ClearAnyOfOurText();
			lc::hold::ReplyEnded();
			g_active = false;
			RestoreSubtitles();
			LC_MSG("Reply finished (%s): replaced %d placeholder(s), %d of %d part(s) started, kept our text up %d time(s)%s",
				why, g_replacements, g_part + 1, g_chunkCount, g_overrides, g_isFiller ? " [filler]" : "");
			if (g_pending.has && (strcmp(why, "player left") == 0 || strcmp(why, "NPC gone") == 0))
			{
				LC_MSG("Say: dropping the reply waiting behind the filler (%s)", why);
				g_pending = PendingReply();
			}
		}

		// Says part `index` (0-based). Returns false if the NPC is gone.
		bool StartPart(int index, bool afterChatter = false)
		{
			TESObjectREFR* ref = FindRef(g_refId);
			if (!ref || !ref->baseForm || ref->baseForm->typeID != kFormType_NPC)
			{
				LC_WARN("Say: %s isn't a loaded NPC any more (part %d)", g_refId.c_str(), index + 1);
				return false;
			}
			std::string why;
			if (!afterChatter && SomeoneSpeaking(ref, &why))
			{
				// Mid-line in a chatter conversation (theirs or their partner's):
				// wait for the line to end.
				g_part = index;
				g_partShown = false;
				g_chatterWaitPart = index;
				g_chatterWaitSince = GetTickCount64();
				LC_MSG("Say: %s: %s; part %d waits for the line to end", g_refId.c_str(), why.c_str(), index + 1);
				return true;
			}
			// Pick part index+1's line (the .esp's conditions test the LCPart
			// global), then SayTo <target> <topic> <force subtitles>. SayTo's
			// result isn't trusted on its own: Tick() watches the subtitle.
			const bool globalOk = SetPartGlobal(index + 1);
			char script[128];
			sprintf_s(script, "SayTo player %s 1", kTopicEditorId);
			const bool ok = g_console->RunScriptLine2(script, ref, true);
			LC_MSG("Say: part %d of %d (%.1fs): %s = %d (%s), %s on %s -> %s (%s)", index + 1, g_chunkCount, g_seconds[index],
				kPartGlobal, index + 1, globalOk ? "set" : "NOT set", script, g_refId.c_str(), ok ? "ran" : "reported failure",
				lc::chatter::Describe(ref).c_str());
			g_part = index;
			g_partShown = false;
			g_partStarted = GetTickCount64();
			g_blankSince = 0;
			g_voiceSeen = false;
			// Safety net from here: the rest of the reply plus generous slack per part.
			double rest = 0;
			for (int i = index; i < g_chunkCount; ++i)
				rest += g_seconds[i];
			g_until = g_partStarted + static_cast<ULONGLONG>(rest * 1000) + kExtraWatchMs
				+ (g_chunkCount - index) * (kPartOverrunMs + kPartGapMs);
			return true;
		}

		// Starts part `index`: at once for an unvoiced reply; a voiced one
		// first waits for the part's audio (Tick() carries on).
		bool BeginPart(int index)
		{
			if (!g_voiced)
				return StartPart(index);
			g_part = index;
			g_partShown = false;
			g_waitingPart = index;
			g_waitSince = GetTickCount64();
			return true;
		}

		// A voiced part whose wait is over: use its audio if it came, else say
		// it silently. Returns false if the NPC is gone.
		bool FinishWaiting(ULONGLONG now)
		{
			const int i = g_waitingPart;
			g_waitingPart = -1;
			const ULONGLONG waited = now - g_waitSince;
			if (g_audio[i] == Audio::Ready && CopyVoiceAudio(i))
			{
				g_seconds[i] = g_audioSeconds[i] + kVoiceTailSeconds;
				LC_MSG("Voice: part %d's audio ready after %llu ms of waiting", i + 1, static_cast<unsigned long long>(waited));
			}
			else if (g_audio[i] == Audio::Failed)
				LC_WARN("Voice: part %d couldn't be voiced; saying it silently", i + 1);
			else if (g_audio[i] == Audio::Ready)
				LC_WARN("Voice: part %d's audio couldn't be copied; saying it silently", i + 1);
			else
				LC_WARN("Voice: part %d's audio didn't come within %llu ms; saying it silently", i + 1,
					static_cast<unsigned long long>(kAudioWaitMs));
			return StartPart(i);
		}
	}

	void AudioReady(const std::string& refIdHex, int turn, int part, const std::string& utf8Path,
		double seconds, const std::string& error, const std::string& utf8LipPath)
	{
		if (g_pending.has && refIdHex == g_pending.refId && turn == g_pending.turn
			&& part >= 1 && part <= static_cast<int>(g_pending.chunks.size()) && part <= kMaxChunks)
		{
			const int i = part - 1;
			if (!error.empty() || utf8Path.empty() || seconds <= 0)
				g_pending.audio[i] = Audio::Failed;
			else
			{
				g_pending.audio[i] = Audio::Ready;
				g_pending.path[i] = Widen(utf8Path, CP_UTF8);
				g_pending.lip[i] = utf8LipPath.empty() ? std::wstring() : Widen(utf8LipPath, CP_UTF8);
				g_pending.seconds[i] = seconds;
			}
			LC_MSG("Voice: part %d's audio kept for the reply waiting behind the filler", part);
			return;
		}
		if (!g_active || !g_voiced || refIdHex != g_refId || turn != g_turn || part < 1 || part > g_chunkCount)
		{
			LC_MSG("Voice: audio for %s turn %d part %d isn't for the reply showing now (ignored)",
				refIdHex.c_str(), turn, part);
			return;
		}
		const int i = part - 1;
		if (!error.empty() || utf8Path.empty() || seconds <= 0)
		{
			LC_WARN("Voice: the companion couldn't voice part %d: %s", part, error.empty() ? "no audio" : error.c_str());
			g_audio[i] = Audio::Failed;
			return;
		}
		g_audioPath[i] = Widen(utf8Path, CP_UTF8);
		g_lipPath[i] = utf8LipPath.empty() ? std::wstring() : Widen(utf8LipPath, CP_UTF8);
		g_audioSeconds[i] = seconds;
		g_audio[i] = Audio::Ready;
		LC_MSG("Voice: part %d's audio is ready (%.1fs): %s%s", part, seconds, utf8Path.c_str(),
			utf8LipPath.empty() ? " (no lip file)" : " (with a lip file)");
	}

	bool Say(const std::string& refIdHex, const std::vector<std::string>& utf8Chunks, bool voiced, int turn, bool filler,
		bool streaming)
	{
		if (g_active && g_isFiller && !filler && refIdHex == g_refId)
		{
			g_pending = PendingReply();
			g_pending.has = true;
			g_pending.refId = refIdHex;
			g_pending.chunks = utf8Chunks;
			g_pending.voiced = voiced;
			g_pending.turn = turn;
			g_pending.streaming = streaming;
			LC_MSG("Say: the reply arrived while the filler plays; it starts as soon as the filler ends");
			return true;
		}
		if (g_active && !g_isFiller && filler)
		{
			LC_MSG("Say: a filler arrived while a reply is already playing (ignored)");
			return true;
		}
		if (!g_console || !g_console->RunScriptLine2)
		{
			LC_ERR("Can't make NPCs speak: console interface missing");
			return false;
		}
		TESObjectREFR* ref = FindRef(refIdHex);
		if (!ref || !ref->baseForm || ref->baseForm->typeID != kFormType_NPC)
		{
			LC_WARN("Say: %s isn't a loaded NPC any more", refIdHex.c_str());
			return false;
		}

		// Up to kMaxChunks chunks; any extra ones are merged into the last.
		const std::vector<std::string> chunks = FitChunks(utf8Chunks);
		if (chunks.empty())
		{
			LC_WARN("Say: empty reply for %s; nothing to say", refIdHex.c_str());
			return true;
		}

		g_refId = refIdHex;
		g_chunkCount = static_cast<int>(chunks.size());
		g_utf8Text.clear();
		double total = 0;
		for (int i = 0; i < g_chunkCount; ++i)
		{
			const std::string text = ToGameText(chunks[i]);
			strncpy_s(g_chunks[i], text.c_str(), _TRUNCATE);
			g_seconds[i] = ReadingSeconds(text.size());
			total += g_seconds[i];
			g_utf8Text += (i ? " " : "") + chunks[i];
		}
		// Silent files first, for every reply: a voiced part whose audio never
		// comes is still said, silently, with its subtitle.
		WriteVoiceFiles(OBLIVION_CAST(ref->baseForm, TESForm, TESNPC), 1, g_chunkCount, g_seconds);
		g_voiced = voiced;
		g_turn = turn;
		g_isFiller = filler;
		g_partsKnown = !streaming;
		g_partsWaitSince = 0;
		g_waitingPart = -1;
		g_chatterWaitPart = -1;
		g_chatterRetries = 0;
		for (int i = 0; i < kMaxChunks; ++i)
		{
			g_audio[i] = Audio::Waiting;
			g_audioPath[i].clear();
			g_lipPath[i].clear();
			g_audioSeconds[i] = 0;
		}
		LC_MSG("Say: reply to %s in %d part(s)%s, %.1fs of reading in all, %s", refIdHex.c_str(), g_chunkCount,
			streaming ? " so far (streamed: the rest follows)" : "", total, voiced ? "voiced (waiting for audio)" : "silent");
		for (int i = 0; i < g_chunkCount; ++i)
			LC_MSG("  chunk %d (%.1fs): %s", i + 1, g_seconds[i], g_chunks[i]);

		SubtitlesOnForReply();
		const ULONGLONG now = GetTickCount64();
		g_active = true;
		g_started = now;
		// Safety net: the whole reply plus generous slack per part.
		g_until = now + static_cast<ULONGLONG>(total * 1000) + kExtraWatchMs + g_chunkCount * (kPartOverrunMs + kPartGapMs)
			+ (streaming ? kAudioWaitMs : 0);
		g_fallbackDone = false;
		g_dumped = false;
		g_textTile = nullptr;
		g_lastSeen[0] = '\0';
		g_changesLogged = 0;
		g_subtitleBlank = false;
		g_replacedOnce = false;
		g_replacements = 0;
		g_overrides = 0;
		lc::hold::ReplyStarted(refIdHex);
		BeginPart(0);
		return true;
	}

	void PartsReady(const std::string& refIdHex, int turn, const std::vector<std::string>& utf8Chunks)
	{
		const std::vector<std::string> chunks = FitChunks(utf8Chunks);
		if (g_pending.has && refIdHex == g_pending.refId && turn == g_pending.turn)
		{
			if (!chunks.empty())
				g_pending.chunks = chunks;
			g_pending.partsKnown = true;
			LC_MSG("Say: all %d part(s) known for the reply waiting behind the filler", static_cast<int>(g_pending.chunks.size()));
			return;
		}
		if (!g_active || refIdHex != g_refId || turn != g_turn || g_partsKnown)
		{
			LC_MSG("Say: reply_parts for %s turn %d isn't for the streamed reply showing now (ignored)", refIdHex.c_str(), turn);
			return;
		}
		g_partsKnown = true;
		const int n = std::max(1, static_cast<int>(chunks.size()));
		if (n > 1)
		{
			// Part 1 is already playing or waiting for its audio; only the later parts are new.
			double rest = 0;
			for (int i = 1; i < n; ++i)
			{
				const std::string text = ToGameText(chunks[i]);
				strncpy_s(g_chunks[i], text.c_str(), _TRUNCATE);
				g_seconds[i] = ReadingSeconds(text.size());
				rest += g_seconds[i];
				g_utf8Text += " " + chunks[i];
			}
			TESObjectREFR* ref = FindRef(g_refId);
			if (ref && ref->baseForm && ref->baseForm->typeID == kFormType_NPC)
				WriteVoiceFiles(OBLIVION_CAST(ref->baseForm, TESForm, TESNPC), 2, n, g_seconds);
			g_chunkCount = n;
			const ULONGLONG latest = GetTickCount64() + static_cast<ULONGLONG>((rest + g_seconds[0]) * 1000) + kExtraWatchMs
				+ n * (kPartOverrunMs + kPartGapMs) + kAudioWaitMs;
			g_until = std::max(g_until, latest);
		}
		LC_MSG("Say: the whole reply is %d part(s)%s", n, g_partsWaitSince ? " (part 1 was waiting for them)" : "");
		for (int i = 1; i < n; ++i)
			LC_MSG("  chunk %d (%.1fs): %s", i + 1, g_seconds[i], g_chunks[i]);
	}

	void Tick()
	{
		if (!g_active)
		{
			if (g_pending.has)
			{
				PendingReply next = g_pending;
				g_pending = PendingReply();
				LC_MSG("Say: filler done; starting the reply that waited behind it");
				if (Say(next.refId, next.chunks, next.voiced, next.turn, false, next.streaming && !next.partsKnown))
				{
					for (int i = 0; i < kMaxChunks && i < g_chunkCount; ++i)
						if (next.audio[i] != Audio::Waiting)
						{
							g_audio[i] = next.audio[i];
							g_audioPath[i] = next.path[i];
							g_lipPath[i] = next.lip[i];
							g_audioSeconds[i] = next.seconds[i];
						}
				}
				else
				{
					std::string text;
					for (const std::string& c : next.chunks)
						text += (text.empty() ? "" : " ") + c;
					lc::notify::Queue(text);
				}
			}
			return;
		}
		const bool ok = GuardedTick();
		const ULONGLONG now = GetTickCount64();
		if (!ok)
		{
			EndReply("subtitle check failed");
			return;
		}
		// The player went somewhere else (through a door, or far away): the
		// conversation is over, as with vanilla dialogue.
		if (!SpeakerStillNear(FindRef(g_refId)))
		{
			EndReply("player left");
			return;
		}

		// A part waiting for the NPC's chatter line to end.
		if (g_chatterWaitPart >= 0)
		{
			const bool speaking = SomeoneSpeaking(FindRef(g_refId), nullptr);
			const ULONGLONG waited = now - g_chatterWaitSince;
			if (!speaking || waited > kChatterWaitMs)
			{
				const int i = g_chatterWaitPart;
				g_chatterWaitPart = -1;
				if (speaking)
					LC_WARN("Say: still speaking after %llu ms; saying part %d anyway", static_cast<unsigned long long>(waited), i + 1);
				else
					LC_MSG("Say: the other line ended after %llu ms; saying part %d now", static_cast<unsigned long long>(waited), i + 1);
				if (!StartPart(i, true))
					EndReply("NPC gone");
			}
			return;
		}

		// A voiced part waiting for its audio.
		if (g_waitingPart >= 0)
		{
			const int i = g_waitingPart;
			if (g_audio[i] != Audio::Waiting || now - g_waitSince > kAudioWaitMs)
			{
				if (!FinishWaiting(now))
					EndReply("NPC gone");
			}
			return;
		}

		// A part whose subtitle never appeared: show what's left as a notification.
		if (!g_partShown && now - g_partStarted > kFallbackMs)
		{
			TESObjectREFR* speaker = FindRef(g_refId);
			if (g_chatterRetries < kMaxChatterRetries && lc::chatter::IsTalking(speaker) == 1)
			{
				// They started another line instead (chatter): try again once it ends.
				++g_chatterRetries;
				LC_WARN("Part %d's subtitle didn't appear and %s is speaking another line (%s); trying again when it ends (retry %d)",
					g_part + 1, g_refId.c_str(), lc::chatter::Describe(speaker).c_str(), g_chatterRetries);
				g_chatterWaitPart = g_part;
				g_chatterWaitSince = now;
				return;
			}
			std::string rest;
			for (int i = g_part; i < g_chunkCount; ++i)
				rest += (rest.empty() ? "" : " ") + std::string(g_chunks[i]);
			LC_WARN("Part %d's subtitle didn't appear within %llu ms; showing the rest as a notification",
				g_part + 1, static_cast<unsigned long long>(now - g_partStarted));
			g_fallbackDone = true;
			lc::notify::Queue(g_part == 0 ? g_utf8Text : rest);
			EndReply("subtitle didn't appear");
			return;
		}

		// A part lasts its reading time from when its text first appears. Until
		// then our text stays in the subtitle tile, even if another NPC's line
		// lands there or the game clears it early (an NPC greeting the player
		// can cut our line short). Then the next part is said, if any.
		if (g_partShown)
		{
			if (!g_voiceSeen)
			{
				const int talking = lc::chatter::IsTalking(FindRef(g_refId));
				const ULONGLONG since = now - g_partStarted;
				if (talking == 1 || talking < 0 || since > kVoiceStartWaitMs)
				{
					g_voiceSeen = true;
					g_blankSince = now;          // the part's time starts with the voice
					g_until += since;            // and the safety net moves with it
					if (talking != 1 && talking >= 0)
						LC_WARN("Say: no sign of part %d's voice after %llu ms; showing the text anyway", g_part + 1,
							static_cast<unsigned long long>(since));
					else if (since > kVoiceGraceMs)
						LC_MSG("Say: part %d's voice started %llu ms after its subtitle; the text waited for it", g_part + 1,
							static_cast<unsigned long long>(since));
				}
				else
				{
					if (since > kVoiceGraceMs)
						HideOurSubtitle();
					return;
				}
			}
			if (!g_blankSince)
				g_blankSince = now;          // here: when this part's text first showed
			const ULONGLONG shownFor = now - g_blankSince;
			if (shownFor < static_cast<ULONGLONG>(g_seconds[g_part] * 1000))
			{
				if (!KeepOurSubtitle())
					EndReply("subtitle check failed");
				return;
			}
			if (g_part + 1 < g_chunkCount)
			{
				if (!BeginPart(g_part + 1))
					EndReply("NPC gone");
			}
			else if (!g_partsKnown)
			{
				// Streamed: the rest of the reply hasn't been listed yet.
				if (!g_partsWaitSince)
				{
					g_partsWaitSince = now;
					LC_MSG("Say: part %d is over; waiting for the rest of the reply", g_part + 1);
				}
				else if (now - g_partsWaitSince > kAudioWaitMs)
				{
					LC_WARN("Say: the rest of the reply didn't come within %llu ms; ending after part %d",
						static_cast<unsigned long long>(kAudioWaitMs), g_part + 1);
					ClearOurSubtitle();
					EndReply("rest of the reply didn't come");
				}
			}
			else
			{
				ClearOurSubtitle();
				EndReply("all parts said");
			}
			return;
		}

		if (now > g_until)
			EndReply("timed out");
	}
}
