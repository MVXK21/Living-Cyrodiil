#pragma once

#include <string>

// Starts the companion with the game (alpha release, MVXK21, Nov 13, 2026:
// "install, open in explorer, type in api key, refresh, play").
//
// The release zip holds the companion and its own Python beside the plugin:
//   <mod>\OBSE\Plugins\LivingCyrodiil.dll
//   <mod>\LCCompanion\python\python.exe   (+ release.txt, the code, the data)
// The plugin finds the DLL's real folder (under MO2 the game sees a virtual
// Data folder), starts the bundled Python there with no window, and puts it in
// a job object so it closes with the game, even after a crash.
// Nothing is started when there's no bundled companion (a development setup)
// or when a companion already answers on the port.
namespace lc::launch
{
	// Returns a problem to show the player once a save loads ("" = none).
	std::string Start(void* pluginModule, unsigned short port);
}
