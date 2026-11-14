#include "type_box.h"
#include "log.h"

#include "obse/GameAPI.h"
#include "obse/GameMenus.h"
#include "obse/GameTiles.h"

#include <windows.h>

#include <cstring>

namespace lc::typebox
{
	namespace
	{
		const ULONGLONG kOpenGraceMs = 1000;    // the window may take a frame or two to appear

		// The game's own function that opens the window (1.2.0416). It's
		// __cdecl (it ends in a plain "ret"): xOBSE's
		// InterfaceManager::CreateTextEditMenu calls it as a member function,
		// which leaves the stack 8 bytes off and crashed the game (0.13.0 spike).
		typedef bool (__cdecl* CreateTextEditMenu_t)(const char* prompt, const char* defaultText);
		const CreateTextEditMenu_t CreateTextEditMenuFn = reinterpret_cast<CreateTextEditMenu_t>(0x005DCF20);

		// Where the window puts its text when OK is clicked: a game string
		// (BSStringT) at 0x00B3B738 (TextEditMenu's click handler, 0x005DD1B0).
		// Back closes the window without touching it.
		struct GameString { char* data; unsigned short length; unsigned short capacity; };
		GameString* const kResult = reinterpret_cast<GameString*>(0x00B3B738);
		char      g_resultBefore[1024] = {};     // the result string when we opened the window
		char      g_prompt[256] = {};            // kept for the window's lifetime, just in case

		// The window's text editor sits at +0x34 in the menu; its +0x0C is the
		// widest the text may get, in pixels (the text tile's wrapwidth - 5,
		// set when the window opens, 0x00583DD0). That kept typing to one
		// line (about 80 characters). -1 means no limit (0x005800A3): the
		// text wraps onto more lines, and NorthernUI's box grows to fit.
		const size_t kMaxWidthOffset = 0x34 + 0x0C;
		bool      g_limitLifted = false;
		// Enter doesn't send in the game's window (only the OK button, or the
		// gamepad's A in NorthernUI); the plugin presses OK itself.
		const UInt32 kOkButtonId = 2;            // TextEditMenu click ids: 2 = OK, 3 = Back
		bool      g_enterWasDown = false;
		bool      g_enterSent = false;

		// Lifts the width limit. No C++ objects: runs in __try.
		bool LiftLimit(TextEditMenu* menu, int* was)
		{
			__try
			{
				int* width = reinterpret_cast<int*>(reinterpret_cast<char*>(menu) + kMaxWidthOffset);
				*was = *width;
				*width = -1;
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		// Presses the window's OK button. No C++ objects: runs in __try.
		bool PressOk(TextEditMenu* menu)
		{
			__try
			{
				menu->HandleClick(kOkButtonId, nullptr);
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Text box: crash avoided while pressing OK (exception %08X)", GetExceptionCode());
				return false;
			}
		}

		// What the player typed: without the cursor the window shows (a "|"
		// or, in 0.13.3's test, a 0x7F glyph) and the space it starts with.
		std::string Clean(const char* s)
		{
			std::string t;
			for (const char* c = s ? s : ""; *c; ++c)
				if (*c != 0x7F)
					t += *c;
			if (!t.empty() && t.back() == '|')
				t.pop_back();
			const size_t a = t.find_first_not_of(" 	");
			if (a == std::string::npos)
				return {};
			const size_t b = t.find_last_not_of(" 	");
			return t.substr(a, b - a + 1);
		}

		bool      g_open = false;               // we opened it and haven't seen it close yet
		bool      g_seen = false;               // it has appeared
		ULONGLONG g_openedAt = 0;
		char      g_text[1024] = {};             // its text, as last read
		bool      g_dumped = false;
		// How it closed: the last of these keys seen down while it was open.
		bool      g_enterSeen = false, g_escapeSeen = false, g_clickSeen = false;

		bool KeyDown(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }

		const char* TileString(Tile* t)
		{
			const char* s = nullptr;
			if (t && t->GetStringValue(kTileValue_string, &s) && s)
				return s;
			return nullptr;
		}

		// The window's tiles, once, so the log shows how it's built (for
		// finding the text and buttons with other UI mods).
		void DumpTiles(Tile* t, int depth, int* count)
		{
			if (!t || depth > 8 || ++(*count) > 80) return;
			const char* name = t->name.m_data ? t->name.m_data : "?";
			const char* s = TileString(t);
			LC_MSG("  text box %*s%s%s%s%s", depth * 2, "", name, s ? " = \"" : "", s ? s : "", s ? "\"" : "");
			for (auto* n = t->childList.start; n; n = n->next)
				DumpTiles(n->data, depth + 1, count);
		}

		// The window if it's showing, else null. No C++ objects: runs in __try.
		TextEditMenu* FindMenu()
		{
			__try
			{
				return static_cast<TextEditMenu*>(GetMenuByType(kMenuType_TextEdit));
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return nullptr;
			}
		}

		// Copies the typed text into `out`. No C++ objects: runs in __try.
		bool ReadText(TextEditMenu* menu, char* out, size_t size)
		{
			__try
			{
				const char* s = menu && menu->text ? TileString(menu->text) : nullptr;
				if (!s)
					return false;
				strncpy_s(out, size, s, _TRUNCATE);
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		// Empties the game's OK result string, so after the window closes any
		// text in it means OK was pressed. No C++ objects: runs in __try.
		bool ClearResult()
		{
			__try
			{
				if (kResult->data && kResult->capacity)
					kResult->data[0] = 0;
				kResult->length = 0;
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		// Copies the game's OK result string. No C++ objects: runs in __try.
		bool ReadResult(char* out, size_t size)
		{
			out[0] = 0;
			__try
			{
				if (kResult->data && kResult->length)
					strncpy_s(out, size, kResult->data, _TRUNCATE);
				return true;
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				return false;
			}
		}

		bool CallOpen(const char* prompt)
		{
			__try
			{
				return CreateTextEditMenuFn(prompt, "");
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_ERR("Text box: crash avoided while opening it (exception %08X)", GetExceptionCode());
				return false;
			}
		}

		void DumpOnce(TextEditMenu* menu)
		{
			if (g_dumped || !menu)
				return;
			g_dumped = true;
			int count = 0;
			__try
			{
				LC_MSG("Text box: the window's tiles (one-time map, for diagnosis):");
				DumpTiles(menu->tile, 0, &count);
			}
			__except (EXCEPTION_EXECUTE_HANDLER)
			{
				LC_WARN("Text box: couldn't map the window's tiles");
			}
		}
	}

	bool Open(const std::string& prompt)
	{
		if (g_open)
			return false;
		strncpy_s(g_prompt, prompt.c_str(), _TRUNCATE);
		ReadResult(g_resultBefore, sizeof(g_resultBefore));
		if (!ClearResult())
			LC_WARN("Text box: couldn't empty the game's result string");
		const bool ok = CallOpen(g_prompt);
		LC_MSG("Text box: CreateTextEditMenu(\"%s\") -> %s", prompt.c_str(), ok ? "true" : "false");
		g_open = true;                 // even on false: see whether it shows anyway
		g_seen = false;
		g_openedAt = GetTickCount64();
		g_text[0] = 0;
		g_enterSeen = g_escapeSeen = g_clickSeen = false;
		g_limitLifted = false;
		g_enterWasDown = true;          // ignore an Enter already held as the window opens
		g_enterSent = false;
		return ok;
	}

	bool IsOpen() { return g_open; }

	bool Tick(std::string* text, bool* sent)
	{
		if (!g_open)
			return false;
		TextEditMenu* menu = FindMenu();
		if (menu)
		{
			if (!g_seen)
			{
				g_seen = true;
				LC_MSG("Text box: the window is showing (%llu ms after opening)",
					static_cast<unsigned long long>(GetTickCount64() - g_openedAt));
				DumpOnce(menu);
			}
			if (!g_limitLifted)
			{
				int was = 0;
				g_limitLifted = true;
				if (LiftLimit(menu, &was))
					LC_MSG("Text box: length limit lifted (it was %d pixels wide)", was);
				else
					LC_WARN("Text box: couldn't lift the length limit");
			}
			char now[1024] = {};
			if (ReadText(menu, now, sizeof(now)) && strcmp(now, g_text) != 0)
			{
				strcpy_s(g_text, now);
				LC_MSG("Text box: text is now \"%s\"", g_text);
			}
			const bool enter = KeyDown(VK_RETURN);
			if (enter) g_enterSeen = true;
			if (enter && !g_enterWasDown && !g_enterSent)
			{
				g_enterSent = true;
				LC_MSG("Text box: Enter pressed; pressing OK");
				PressOk(menu);
			}
			g_enterWasDown = enter;
			if (KeyDown(VK_ESCAPE)) g_escapeSeen = true;
			if (KeyDown(VK_LBUTTON)) g_clickSeen = true;
			return false;
		}
		if (!g_seen)
		{
			if (GetTickCount64() - g_openedAt < kOpenGraceMs)
				return false;
			LC_WARN("Text box: the window never appeared");
			g_open = false;
			return false;
		}
		// It was showing and now it's gone. OK writes the text into the
		// game's result string; Back leaves it alone.
		g_open = false;
		char result[1024] = {};
		const bool readOk = ReadResult(result, sizeof(result));
		const bool changed = readOk && strcmp(result, g_resultBefore) != 0;
		// The result was emptied when the window opened, and only OK fills it.
		(void)changed;
		const std::string typed = Clean(g_text), given = Clean(result);
		*sent = readOk && !given.empty();
		*text = *sent ? given : typed;
		LC_MSG("Text box: closed. Box text \"%s\", game result \"%s\" (was \"%s\"%s); keys seen while open: "
			"Enter %s, Escape %s, mouse click %s -> %s",
			g_text, result, g_resultBefore, readOk ? "" : ", couldn't read it", g_enterSeen ? "yes" : "no",
			g_escapeSeen ? "yes" : "no", g_clickSeen ? "yes" : "no", *sent ? "sent" : "cancelled");
		return true;
	}
}
