#include "key_names.h"

#include <cctype>
#include <cstdlib>

namespace lc::keys
{
	namespace
	{
		struct Key { const char* name; unsigned short code; };

		// First name per code is the one shown back to the player.
		const Key kKeys[] = {
			{ "Escape", 1 }, { "1", 2 }, { "2", 3 }, { "3", 4 }, { "4", 5 }, { "5", 6 }, { "6", 7 },
			{ "7", 8 }, { "8", 9 }, { "9", 10 }, { "0", 11 }, { "Minus", 12 }, { "Equals", 13 },
			{ "Backspace", 14 }, { "Tab", 15 },
			{ "Q", 16 }, { "W", 17 }, { "E", 18 }, { "R", 19 }, { "T", 20 }, { "Y", 21 }, { "U", 22 },
			{ "I", 23 }, { "O", 24 }, { "P", 25 }, { "LeftBracket", 26 }, { "RightBracket", 27 },
			{ "Enter", 28 }, { "Return", 28 }, { "LeftCtrl", 29 }, { "LeftControl", 29 },
			{ "A", 30 }, { "S", 31 }, { "D", 32 }, { "F", 33 }, { "G", 34 }, { "H", 35 }, { "J", 36 },
			{ "K", 37 }, { "L", 38 }, { "Semicolon", 39 }, { "Apostrophe", 40 },
			{ "Tilde", 41 }, { "Grave", 41 }, { "LeftShift", 42 }, { "Backslash", 43 },
			{ "Z", 44 }, { "X", 45 }, { "C", 46 }, { "V", 47 }, { "B", 48 }, { "N", 49 }, { "M", 50 },
			{ "Comma", 51 }, { "Period", 52 }, { "Slash", 53 }, { "RightShift", 54 },
			{ "NumpadMultiply", 55 }, { "NumpadStar", 55 }, { "LeftAlt", 56 }, { "Alt", 56 },
			{ "Space", 57 }, { "Spacebar", 57 }, { "CapsLock", 58 },
			{ "F1", 59 }, { "F2", 60 }, { "F3", 61 }, { "F4", 62 }, { "F5", 63 }, { "F6", 64 },
			{ "F7", 65 }, { "F8", 66 }, { "F9", 67 }, { "F10", 68 }, { "NumLock", 69 }, { "ScrollLock", 70 },
			{ "Numpad7", 71 }, { "Numpad8", 72 }, { "Numpad9", 73 }, { "NumpadMinus", 74 },
			{ "Numpad4", 75 }, { "Numpad5", 76 }, { "Numpad6", 77 }, { "NumpadPlus", 78 },
			{ "Numpad1", 79 }, { "Numpad2", 80 }, { "Numpad3", 81 }, { "Numpad0", 82 },
			{ "NumpadPeriod", 83 }, { "NumpadDecimal", 83 }, { "F11", 87 }, { "F12", 88 },
			{ "NumpadEnter", 156 }, { "RightCtrl", 157 }, { "RightControl", 157 },
			{ "NumpadDivide", 181 }, { "NumpadSlash", 181 }, { "RightAlt", 184 }, { "AltGr", 184 },
			{ "Home", 199 }, { "Up", 200 }, { "UpArrow", 200 }, { "PageUp", 201 }, { "Left", 203 },
			{ "LeftArrow", 203 }, { "Right", 205 }, { "RightArrow", 205 }, { "End", 207 },
			{ "Down", 208 }, { "DownArrow", 208 }, { "PageDown", 209 }, { "Insert", 210 }, { "Delete", 211 },
			{ "Mouse1", 256 }, { "LeftMouse", 256 }, { "Mouse2", 257 }, { "RightMouse", 257 },
			{ "Mouse3", 258 }, { "MiddleMouse", 258 }, { "Mouse4", 259 }, { "Mouse5", 260 },
			{ "Mouse6", 261 }, { "Mouse7", 262 }, { "Mouse8", 263 },
		};

		std::string Normal(const std::string& s)
		{
			std::string out;
			for (char c : s)
				if (!std::isspace(static_cast<unsigned char>(c)) && c != '_' && c != '-')
					out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
			return out;
		}
	}

	unsigned short Parse(const std::string& text)
	{
		const std::string key = Normal(text);
		if (key.empty())
			return 0;
		// A plain number of two or more digits is a raw code, as in earlier
		// versions ("47"); a single digit is the number key ("4").
		if (key.size() > 1 && key.find_first_not_of("0123456789") == std::string::npos)
		{
			const unsigned long n = std::strtoul(key.c_str(), nullptr, 10);
			return (n >= 1 && n <= 263) ? static_cast<unsigned short>(n) : 0;
		}
		for (const Key& k : kKeys)
			if (Normal(k.name) == key)
				return k.code;
		return 0;
	}

	std::string Name(unsigned short code)
	{
		for (const Key& k : kKeys)
			if (k.code == code)
				return k.name;
		return "key " + std::to_string(code);
	}
}
