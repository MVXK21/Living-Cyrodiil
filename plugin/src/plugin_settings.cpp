#include "plugin_settings.h"
#include "key_names.h"
#include "log.h"

#include <string>

namespace lc
{
	PluginSettings LoadSettings(void* moduleHandle)
	{
		PluginSettings s;

		char dllPath[MAX_PATH] = {};
		if (!GetModuleFileNameA(static_cast<HMODULE>(moduleHandle), dllPath, MAX_PATH))
		{
			LC_WARN("Couldn't find our own DLL path; using default settings");
			return s;
		}
		std::string ini(dllPath);
		size_t slash = ini.find_last_of("\\/");
		ini = (slash == std::string::npos ? "" : ini.substr(0, slash + 1)) + "LivingCyrodiil.ini";

		if (GetFileAttributesA(ini.c_str()) == INVALID_FILE_ATTRIBUTES)
		{
			LC_MSG("Settings: %s not found; using defaults", ini.c_str());
		}
		else
		{
			LC_MSG("Settings: reading %s", ini.c_str());
		}

		UINT port = GetPrivateProfileIntA("Companion", "Port", s.companionPort, ini.c_str());
		if (port >= 1024 && port <= 65535)
			s.companionPort = static_cast<unsigned short>(port);
		else
			LC_WARN("Settings: Port %u is out of range (1024-65535); using %u", port, s.companionPort);

		s.autoStartCompanion = GetPrivateProfileIntA("Companion", "AutoStart", 1, ini.c_str()) != 0;
		s.showGreeting = GetPrivateProfileIntA("General", "ShowGreeting", 1, ini.c_str()) != 0;
		s.showConnection = GetPrivateProfileIntA("General", "ShowConnectionNotices", 1, ini.c_str()) != 0;
		s.chatterAwareness = GetPrivateProfileIntA("General", "ChatterAwareness", 1, ini.c_str()) != 0;

		char keyText[64] = {};
		GetPrivateProfileStringA("Controls", "TalkKey", "F4", keyText, sizeof(keyText), ini.c_str());
		// Allow a note after the value ("TalkKey=F4 ; the F4 key").
		std::string keyName(keyText);
		if (size_t semi = keyName.find(';'); semi != std::string::npos)
			keyName.resize(semi);
		if (unsigned short code = lc::keys::Parse(keyName))
		{
			s.talkKey = code;
			if (code == 256 || code == 257)
				s.warning = "Living Cyrodiil: TalkKey is the " + std::string(code == 256 ? "left" : "right") +
					" mouse button, which also attacks or blocks. Consider another key.";
		}
		else
		{
			LC_WARN("Settings: TalkKey '%s' isn't a key name or code; using F4", keyText);
			s.warning = "Living Cyrodiil: unknown TalkKey '" + keyName + "', using F4. Valid names are listed in LivingCyrodiil.ini.";
		}

		s.blockTalkKey = GetPrivateProfileIntA("Controls", "BlockTalkKey", 1, ini.c_str()) != 0;

		// Typed input (Phase 5): "Off" or empty turns it off.
		char typeText[64] = {};
		GetPrivateProfileStringA("Controls", "TypeKey", "F3", typeText, sizeof(typeText), ini.c_str());
		std::string typeName(typeText);
		if (size_t semi = typeName.find(';'); semi != std::string::npos)
			typeName.resize(semi);
		while (!typeName.empty() && isspace(static_cast<unsigned char>(typeName.back())))
			typeName.pop_back();
		if (typeName.empty() || _stricmp(typeName.c_str(), "off") == 0)
			s.typeKey = 0;
		else if (unsigned short code = lc::keys::Parse(typeName))
			s.typeKey = code;
		else
			LC_WARN("Settings: TypeKey '%s' isn't a key name or code; using F3", typeText);
		if (s.typeKey && s.typeKey == s.talkKey)
		{
			LC_WARN("Settings: TypeKey is the same as TalkKey; typing is off");
			s.typeKey = 0;
		}

		s.logVoiceFiles = GetPrivateProfileIntA("Debug", "LogVoiceFiles", 0, ini.c_str()) != 0;

		LC_MSG("Settings: AutoStart=%d", s.autoStartCompanion);
		LC_MSG("Settings: Port=%u ShowGreeting=%d ShowConnectionNotices=%d TalkKey=%s (%u) BlockTalkKey=%d TypeKey=%s LogVoiceFiles=%d ChatterAwareness=%d",
			s.companionPort, s.showGreeting, s.showConnection, lc::keys::Name(s.talkKey).c_str(), s.talkKey,
			s.blockTalkKey, s.typeKey ? lc::keys::Name(s.typeKey).c_str() : "off", s.logVoiceFiles,
			s.chatterAwareness);
		return s;
	}
}
