#pragma once

// Settings from LivingCyrodiil.ini, which sits next to the DLL in OBSE\Plugins.
// Missing file or keys fall back to defaults, so the plugin always loads.

#include <string>

namespace lc
{
	struct PluginSettings
	{
		unsigned short companionPort = 47833;   // [Companion] Port
		bool           autoStartCompanion = true; // [Companion] AutoStart: start the bundled companion with the game
		bool           showGreeting = true;     // [General] ShowGreeting
		bool           showConnection = true;   // [General] ShowConnectionNotices
		bool           chatterAwareness = true; // [General] ChatterAwareness: NPCs know what they were just chatting about
		unsigned short talkKey = 0x3E;          // [Controls] TalkKey: a key name or code (0x3E = F4; mouse = 256+)
		bool           blockTalkKey = true;     // [Controls] BlockTalkKey: hide the talk and type keys from the game while in the world
		unsigned short typeKey = 0x3D;          // [Controls] TypeKey: opens the text box to type what you say (0x3D = F3; 0 = off)
		bool           logVoiceFiles = false;   // [Debug] LogVoiceFiles: log which voice files the game looks for
		std::string    warning;                 // a problem to show the player once a save loads ("" = none)
	};

	PluginSettings LoadSettings(void* moduleHandle);
}
