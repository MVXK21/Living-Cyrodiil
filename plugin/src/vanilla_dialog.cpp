#include "vanilla_dialog.h"
#include "log.h"

#include "obse/GameAPI.h"
#include "obse/GameForms.h"
#include "obse/GameMenus.h"
#include "obse/GameObjects.h"
#include "obse/GameTiles.h"

#include <cstring>

namespace lc::vanilla
{
	namespace
	{
		LineHandler g_handler;
		char        g_last[2048] = {};       // last line seen, in the game's Windows-1252
		char        g_topic[512] = {};       // the topic the current answer belongs to ("" = greeting)
		char        g_pointed[512] = {};     // the last topic the player pointed at
		bool        g_open = false;          // the dialogue window was open last frame
		bool        g_mapped = false;        // logged the window's layout once (NorthernUI check)
		int         g_mapCount = 0;

		// A tile's text value, or nullptr. No C++ objects: runs inside __try.
		const char* TileString(Tile* t)
		{
			const char* s = nullptr;
			if (t && t->GetStringValue(kTileValue_string, &s) && s && *s)
				return s;
			return nullptr;
		}

		void MapTiles(Tile* t, int depth)
		{
			if (!t || depth > 12 || ++g_mapCount > 400) return;
			const char* name = t->name.m_data ? t->name.m_data : "?";
			const char* s = TileString(t);
			if (s)
				LC_MSG("  dialogue window %*s%s = \"%.120s\"", depth * 2, "", name, s);
			else
				LC_MSG("  dialogue window %*s%s", depth * 2, "", name);
			for (auto* n = t->childList.start; n; n = n->next)
				MapTiles(n->data, depth + 1);
		}

		// The topic the player is pointing at: the tile under the mouse (or with
		// keyboard focus), when it's a topic in the window's list. Its text is
		// the topic's name ("What can you tell me about the Argonians?").
		void ReadPointedTopic(DialogMenu* menu, char* topic, size_t len)
		{
			InterfaceManager* ui = InterfaceManager::GetSingleton();
			if (!ui)
				return;
			Tile* t = ui->activeTile ? ui->activeTile : ui->altActiveTile;
			if (!t || t == reinterpret_cast<Tile*>(menu->dialogText))
				return;
			// A topic tile and its "topic_text" child both carry the topic's name;
			// buttons such as barter or goodbye carry other text, which is fine:
			// only lines that follow a real topic click change the window's text.
			const char* s = TileString(t);
			if (!s)
				return;
			const char* name = t->name.m_data ? t->name.m_data : "";
			if (strncmp(name, "dialog_", 7) == 0 || strncmp(name, "vertical_scroll", 15) == 0)
				return;
			strncpy_s(topic, len, s, _TRUNCATE);
		}

		// Copies the speaker's ref id, the window's current text and the topic
		// being pointed at. Returns false if the window isn't open.
		// No C++ objects: runs inside __try.
		bool ReadWindow(UInt32* refId, char* text, size_t len, char* topic, size_t topicLen, bool* crashed)
		{
			*crashed = false;
			__try
			{
				DialogMenu* menu = static_cast<DialogMenu*>(GetMenuByType(kMenuType_Dialog));
				if (!menu)
					return false;
				ReadPointedTopic(menu, topic, topicLen);
				if (!g_mapped && menu->tile)
				{
					g_mapped = true;
					g_mapCount = 0;
					LC_MSG("Dialogue window layout (one-time map, for diagnosis):");
					MapTiles(reinterpret_cast<Tile*>(menu->tile), 0);
				}
				*refId = menu->speaker ? menu->speaker->refID : 0;
				const char* s = TileString(reinterpret_cast<Tile*>(menu->dialogText));
				strncpy_s(text, len, s ? s : "", _TRUNCATE);
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				*crashed = true;
				return false;
			}
		}

		std::string ToUtf8(const char* text)
		{
			int wlen = MultiByteToWideChar(1252, 0, text, -1, nullptr, 0);
			if (wlen <= 0) return {};
			std::wstring wide(wlen, L'\0');
			MultiByteToWideChar(1252, 0, text, -1, wide.data(), wlen);
			int ulen = WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, nullptr, 0, nullptr, nullptr);
			if (ulen <= 0) return {};
			std::string out(ulen, '\0');
			WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, out.data(), ulen, nullptr, nullptr);
			if (!out.empty() && out.back() == '\0') out.pop_back();
			return out;
		}

		std::string NameOf(UInt32 refId)
		{
			TESForm* form = refId ? LookupFormByID(refId) : nullptr;
			const char* n = form ? GetFullName(form) : nullptr;
			if ((!n || !*n) && form)
			{
				TESObjectREFR* ref = OBLIVION_CAST(form, TESForm, TESObjectREFR);
				n = ref && ref->baseForm ? GetFullName(ref->baseForm) : nullptr;
			}
			return n && *n && strcmp(n, "<no name>") != 0 ? n : "";
		}

		bool IsBlank(const char* s)
		{
			for (; *s; ++s)
				if (*s != ' ' && *s != '\n' && *s != '\r' && *s != '\t')
					return false;
			return true;
		}
	}

	void SetHandler(LineHandler handler)
	{
		g_handler = std::move(handler);
	}

	void Tick()
	{
		UInt32 refId = 0;
		char text[2048] = {};
		char pointed[512] = {};
		bool crashed = false;
		const bool open = ReadWindow(&refId, text, sizeof(text), pointed, sizeof(pointed), &crashed);
		if (crashed)
		{
			static bool reported = false;
			if (!reported)
			{
				reported = true;
				LC_ERR("Crash avoided while reading the dialogue window; vanilla lines won't be remembered this session");
			}
			return;
		}
		if (!open)
		{
			if (g_open)
			{
				g_last[0] = '\0';     // a new conversation starts fresh
				g_topic[0] = '\0';
				g_pointed[0] = '\0';
			}
			g_open = false;
			return;
		}
		const bool firstFrame = !g_open;
		g_open = true;
		if (pointed[0])
			strncpy_s(g_pointed, pointed, _TRUNCATE);      // what the player last pointed at
		if (IsBlank(text) || strcmp(text, g_last) == 0)
			return;
		strncpy_s(g_last, text, _TRUNCATE);
		// The line answers the topic just clicked: the one being pointed at as
		// the text changed. The first line when the window opens is a greeting.
		// Later lines with nothing new pointed at continue the same answer.
		if (firstFrame)
			g_topic[0] = '\0';
		else if (g_pointed[0])
			strncpy_s(g_topic, g_pointed, _TRUNCATE);
		g_pointed[0] = '\0';
		char hex[16];
		sprintf_s(hex, "%08X", refId);
		const std::string name = NameOf(refId);
		LC_MSG("Vanilla line from %s (%s)%s%s: %s", name.c_str(), hex,
			g_topic[0] ? ", topic " : "", g_topic, text);
		if (g_handler && refId)
			g_handler(hex, name, ToUtf8(g_topic), ToUtf8(text));
	}
}
