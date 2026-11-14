#include "notify.h"
#include "log.h"

#include "obse/PluginAPI.h"

#include <deque>
#include <mutex>

namespace lc::notify
{
	namespace
	{
		OBSEConsoleInterface*   g_console = nullptr;
		std::mutex              g_lock;
		std::deque<std::string> g_pending;
		const size_t            kMaxPending = 10;
		const size_t            kMaxChars = 200;     // keeps notifications readable
	}

	void Init(void* consoleInterface)
	{
		g_console = static_cast<OBSEConsoleInterface*>(consoleInterface);
	}

	// Oblivion's strings are Windows-1252, not UTF-8. Convert, replacing
	// characters it can't show, then make the text safe inside Message "...".
	std::string ToGameText(const std::string& utf8Text)
	{
		std::string ansi;
		int wlen = MultiByteToWideChar(CP_UTF8, 0, utf8Text.c_str(), -1, nullptr, 0);
		if (wlen > 0)
		{
			std::wstring wide(wlen, L'\0');
			MultiByteToWideChar(CP_UTF8, 0, utf8Text.c_str(), -1, wide.data(), wlen);
			int alen = WideCharToMultiByte(1252, 0, wide.c_str(), -1, nullptr, 0, "?", nullptr);
			if (alen > 0)
			{
				ansi.resize(alen);
				WideCharToMultiByte(1252, 0, wide.c_str(), -1, ansi.data(), alen, "?", nullptr);
				if (!ansi.empty() && ansi.back() == '\0')
					ansi.pop_back();
			}
		}

		std::string out;
		for (char c : ansi)
		{
			unsigned char u = static_cast<unsigned char>(c);
			if (c == '"')
				out += '\'';                 // would end the Message string
			else if (c == '%')
				out += " percent";           // Message treats % as a format code
			else if (u < 0x20)
				out += ' ';                  // newlines, tabs, control characters
			else
				out += c;
		}
		if (out.size() > kMaxChars)
			out = out.substr(0, kMaxChars - 3) + "...";
		return out;
	}

	void Queue(const std::string& utf8Text)
	{
		std::lock_guard<std::mutex> lock(g_lock);
		if (g_pending.size() >= kMaxPending)
		{
			LC_WARN("Notification dropped (too many queued): %s", utf8Text.c_str());
			return;
		}
		g_pending.push_back(utf8Text);
	}

	void ShowPending(bool inGame)
	{
		if (!inGame || !g_console || !g_console->RunScriptLine2)
			return;

		std::string text;
		{
			std::lock_guard<std::mutex> lock(g_lock);
			if (g_pending.empty())
				return;
			text = std::move(g_pending.front());   // one per frame; the game queues them itself
			g_pending.pop_front();
		}

		std::string shown = ToGameText(text);
		std::string script = "Message \"" + shown + "\"";
		bool ok = g_console->RunScriptLine2(script.c_str(), nullptr, true);
		LC_MSG("Notification %s: %s", ok ? "shown" : "FAILED", shown.c_str());
	}
}
