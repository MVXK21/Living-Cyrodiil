// Living Cyrodiil - xOBSE game plugin
//
// Phase 2 step 3: connects to the companion program over a local socket
// (docs/protocol.md), reports game events to it, and shows its notifications.
// All game work happens on the main thread in Tick(); all socket work happens
// on CompanionLink's background thread. The game never waits on the network.
//
// No console or script commands are registered yet. Commands need an opcode
// range assigned by the xOBSE team. See docs/phase1-findings.md.

#include "obse/PluginAPI.h"
#include "obse/GameAPI.h"

#include "companion_link.h"
#include "companion_launch.h"
#include "json.h"
#include "log.h"
#include "notify.h"
#include "npc_sheet.h"
#include "plugin_settings.h"
#include "speak.h"
#include "file_watch.h"
#include "hold.h"
#include "chatter.h"
#include "awareness.h"
#include "quests.h"
#include "vanilla_dialog.h"
#include "type_box.h"
#include "playthrough.h"
#include "version.h"

#include "obse/GameMenus.h"

namespace
{
	const int kProtocolVersion = 1;
	const ULONGLONG kSettleMs = 2500;     // wait this long in the world before showing anything

	PluginHandle            g_pluginHandle = kPluginHandle_Invalid;
	OBSEMessagingInterface* g_messaging = nullptr;
	OBSETasks2Interface*    g_tasks = nullptr;
	OBSEInputInterface*     g_input = nullptr;
	HMODULE                 g_module = nullptr;

	// Push-to-talk state
	bool        g_keyWasDown = false;
	bool        g_rawKeyWasDown = false;     // the physical key, last frame (any mode)
	bool        g_talking = false;           // key held while looking at an NPC
	std::string g_talkRefId;
	ULONGLONG   g_talkStartMs = 0;

	// Last outdoor place the player was in, so interiors know their city.
	std::string g_lastOutdoorPlace;
	float       g_lastOutdoorX = 0, g_lastOutdoorY = 0;   // where the player last was outdoors
	std::string g_lastOutdoorWorld;                       // ...and in which world ("" = not yet)
	ULONGLONG   g_nextPlaceCheckMs = 0;

	lc::PluginSettings      g_settings;
	lc::CompanionLink       g_link;
	bool                    g_handshakeDone = false;

	// "In game" = a save has loaded and we haven't gone back to the menu.
	bool        g_inGame = false;
	ULONGLONG   g_inGameSinceMs = 0;      // first frame after the load; 0 = not yet seen
	bool        g_loggedFirstTick = false;
	std::string g_lastSaveName;

	// Log goes to "Documents\My Games\Oblivion\LivingCyrodiil\LivingCyrodiil.log".
	// That folder exists for every player, follows OneDrive redirection, and
	// sits outside any mod manager's virtual files.
	// Build mark: a placeholder that tools/make_tester_package.py overwrites in
	// each tester's copy of the DLL with a random code, so a leaked copy can be
	// traced to the copy it came from. It says nothing about the tester or
	// their PC. Keep the length: 7 + 24 characters.
	volatile const char kBuildMark[] = "LCMARK:000000000000000000000000";

	void OpenLog()
	{
		IDebugLog::OpenRelative(CSIDL_PERSONAL, "\\My Games\\Oblivion\\LivingCyrodiil\\LivingCyrodiil.log");
		char mark[32] = {};
		for (int i = 0; i < 24; ++i)
			mark[i] = kBuildMark[7 + i];
		LC_MSG("Living Cyrodiil %s starting (build %s). Copyright (c) 2026 MVXK21. All rights reserved.",
			LC_VERSION_STRING, mark);
	}

	std::string FileNameOnly(const char* path)
	{
		if (!path) return {};
		std::string p(path);
		size_t slash = p.find_last_of("\\/");
		return slash == std::string::npos ? p : p.substr(slash + 1);
	}

	void SendEvent(const char* name, const std::string& detail = {})
	{
		lc::json::Value msg;
		msg.Set("type", "event").Set("name", name);
		if (!detail.empty())
			msg.Set("detail", detail);
		g_link.Send(msg.Dump());
	}

	std::string HelloLine()
	{
		lc::json::Value hello;
		hello.Set("type", "hello")
			.Set("protocol", kProtocolVersion)
			.Set("side", "plugin")
			.Set("plugin_version", LC_VERSION_STRING)
			.Set("game", "oblivion");
		return hello.Dump();
	}

	// ---- messages from the companion (main thread) -----------------------------

	// Which playthrough (and how much of its memory) the loaded save is
	// (Phase 5 memory, docs/phase5-plan.md step 2).
	void SendPlaythrough()
	{
		if (!g_link.IsConnected() || !g_handshakeDone || lc::playthrough::Id().empty())
			return;
		lc::json::Value msg;
		msg.Set("type", "playthrough").Set("id", lc::playthrough::Id())
			.Set("seq", static_cast<double>(lc::playthrough::Seq())).Set("save", g_lastSaveName);
		g_link.Send(msg.Dump());
	}

	// Before a conversation: a new game gets its playthrough id now.
	void EnsurePlaythrough()
	{
		if (lc::playthrough::Ensure())
			SendPlaythrough();
	}

	void HandleCompanionLine(const std::string& line)
	{
		LC_MSG("<- companion: %s", line.c_str());

		lc::json::Value msg;
		std::string error;
		if (!lc::json::Parse(line, msg, error) || !msg.IsObject())
		{
			LC_WARN("Ignoring unreadable message from companion (%s)", error.c_str());
			return;
		}
		const std::string type = msg.GetString("type");

		if (type == "hello")
		{
			int protocol = static_cast<int>(msg.GetNumber("protocol", -1));
			if (protocol != kProtocolVersion)
			{
				LC_ERR("Companion speaks protocol %d, we speak %d", protocol, kProtocolVersion);
				lc::notify::Queue("Living Cyrodiil: the companion and plugin versions don't match. Please update both.");
				return;
			}
			g_handshakeDone = true;
			LC_MSG("Handshake done with companion %s", msg.GetString("companion_version", "?").c_str());
			if (g_settings.showConnection)
				lc::notify::Queue("Living Cyrodiil: companion connected.");
			if (g_inGame && !g_lastSaveName.empty())
				SendEvent("save_loaded", g_lastSaveName);     // catch up a companion started mid-game
			if (g_inGame)
				SendPlaythrough();
		}
		else if (type == "memory_seq")
		{
			lc::playthrough::SetSeq(msg.GetString("id"), static_cast<long long>(msg.GetNumber("seq", -1)));
		}
		else if (type == "notify")
		{
			lc::notify::Queue(msg.GetString("text"));
		}
		else if (type == "reply")
		{
			const std::string ref = msg.GetString("ref_id");
			const std::string text = msg.GetString("text");
			// "chunks" (one subtitle each) is optional; "text" alone is one chunk.
			std::vector<std::string> chunks;
			if (const lc::json::Value* c = msg.Find("chunks"); c && c->type() == lc::json::Value::Type::Array)
				for (const lc::json::Value& part : c->AsArray())
					if (part.IsString() && !part.AsString().empty())
						chunks.push_back(part.AsString());
			if (chunks.empty())
				chunks.push_back(text);
			// "voiced": a reply_audio per part follows (Phase 3), with this turn.
			const bool voiced = msg.GetBool("voiced");
			const int turn = static_cast<int>(msg.GetNumber("turn", 0));
			// "streaming" (step 4b): chunks is part 1 only; reply_parts follows.
			if (!lc::speak::Say(ref, chunks, voiced, turn, msg.GetBool("filler"), msg.GetBool("streaming")))
				lc::notify::Queue(text);     // fallback: at least show the words
		}
		else if (type == "reply_parts")
		{
			std::vector<std::string> chunks;
			if (const lc::json::Value* c = msg.Find("chunks"); c && c->type() == lc::json::Value::Type::Array)
				for (const lc::json::Value& part : c->AsArray())
					if (part.IsString() && !part.AsString().empty())
						chunks.push_back(part.AsString());
			lc::speak::PartsReady(msg.GetString("ref_id"), static_cast<int>(msg.GetNumber("turn", 0)), chunks);
		}
		else if (type == "reply_audio")
		{
			lc::speak::AudioReady(msg.GetString("ref_id"), static_cast<int>(msg.GetNumber("turn", 0)),
				static_cast<int>(msg.GetNumber("part", 0)), msg.GetString("path"), msg.GetNumber("seconds", 0),
				msg.GetString("error"), msg.GetString("lip"));
		}
		else if (type == "error")
		{
			LC_ERR("Companion error %s: %s", msg.GetString("code").c_str(), msg.GetString("text").c_str());
			std::string text = msg.GetString("text");
			if (!text.empty())
				lc::notify::Queue(text);
		}
		else
		{
			LC_MSG("Unknown message type '%s' from companion (ignored)", type.c_str());
		}
	}

	// ---- push-to-talk (main thread) --------------------------------------------

	// Crash-guarded wrapper: reading game structures must never take the game down.
	// (No C++ objects with destructors live in this function, as __try requires.)
	// Records where a caught crash happened, as an offset inside our DLL (or the
	// game), so it can be looked up later against LivingCyrodiil.pdb.
	struct CrashInfo { DWORD code; void* address; };

	int CaptureCrash(EXCEPTION_POINTERS* ep, CrashInfo* out)
	{
		out->code = ep->ExceptionRecord->ExceptionCode;
		out->address = ep->ExceptionRecord->ExceptionAddress;
		return EXCEPTION_EXECUTE_HANDLER;
	}

	void LogCrash(const char* what, const CrashInfo& c)
	{
		HMODULE mod = nullptr;
		char modName[MAX_PATH] = "?";
		GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
			static_cast<LPCSTR>(c.address), &mod);
		if (mod)
		{
			GetModuleFileNameA(mod, modName, MAX_PATH);
			const char* slash = strrchr(modName, '\\');
			if (slash) memmove(modName, slash + 1, strlen(slash + 1) + 1);
		}
		const uintptr_t offset = reinterpret_cast<uintptr_t>(c.address) - reinterpret_cast<uintptr_t>(mod);
		LC_ERR("Crash avoided while %s (exception %08X at %s+0x%X, step: %s). Please report this with the log.",
			what, c.code, modName, static_cast<unsigned>(offset), lc::npc::LastStage());
	}

	bool SafeReadTarget(bool fromDialog, lc::npc::Target* target, lc::json::Value* sheet, std::string* name)
	{
		CrashInfo crash{};
		__try
		{
			*target = fromDialog ? lc::npc::ReadDialogSpeaker(*sheet, *name)
			                     : lc::npc::ReadCrosshairNpc(*sheet, *name);
			return true;
		}
		__except (CaptureCrash(GetExceptionInformation(), &crash))
		{
			LogCrash(fromDialog ? "reading the dialogue window's NPC" : "reading the crosshair NPC", crash);
			return false;
		}
	}

	// ---- talking from the vanilla dialogue window ------------------------------
	// Holding the talk key in the dialogue window talks to its speaker: the
	// window closes and the conversation carries on in the world (DESIGN.md,
	// "Talking from the dialogue window").

	typedef void (*CloseAllMenus_t)(void);
	const CloseAllMenus_t CloseAllMenusFn = reinterpret_cast<CloseAllMenus_t>(0x00579770);   // 1.2.0416, as xOBSE's CloseAllMenus
	const ULONGLONG kDialogCloseGraceMs = 1500;     // the window may take a moment to close
	ULONGLONG g_closingDialogUntil = 0;

	// True when the dialogue window is the menu the player is in (not, say,
	// a barter window opened from it). No C++ objects: runs inside __try.
	bool DialogIsActiveMenu()
	{
		__try
		{
			InterfaceManager* ui = InterfaceManager::GetSingleton();
			if (!ui || !GetMenuByType(kMenuType_Dialog))
				return false;
			Menu* active = ui->activeMenu;
			if (!active)
			{
				Tile* tile = ui->activeTile ? ui->activeTile : ui->altActiveTile;
				active = tile ? tile->GetContainingMenu() : nullptr;
			}
			return !active || active->id == kMenuType_Dialog;
		}
		__except (EXCEPTION_EXECUTE_HANDLER)
		{
			return false;
		}
	}

	bool SafeCloseMenus()
	{
		__try { CloseAllMenusFn(); return true; }
		__except (EXCEPTION_EXECUTE_HANDLER)
		{
			LC_ERR("Crash avoided while closing the dialogue window (exception %08X)", GetExceptionCode());
			return false;
		}
	}

	void OnTalkKeyDown(bool fromDialog = false)
	{
		lc::npc::Target target = lc::npc::Target::Nothing;
		lc::json::Value sheet;
		std::string name;
		if (!SafeReadTarget(fromDialog, &target, &sheet, &name))
		{
			lc::notify::Queue("Living Cyrodiil couldn't read that person. Details are in the log.");
			return;
		}

		LC_MSG("Talk key down: %s is %s", fromDialog ? "dialogue window speaker" : "crosshair",
			lc::npc::TargetText(target));
		if (fromDialog && target == lc::npc::Target::Ok)
		{
			// Close the window and carry on in the world.
			g_closingDialogUntil = GetTickCount64() + kDialogCloseGraceMs;
			LC_MSG("Talking from the dialogue window: closing it");
			SafeCloseMenus();
		}
		switch (target)
		{
		case lc::npc::Target::Ok:
			break;
		case lc::npc::Target::Nothing:
			lc::notify::Queue("There's nobody there to talk to.");
			return;
		case lc::npc::Target::Creature:
			lc::notify::Queue("You can't talk to that creature.");
			return;
		case lc::npc::Target::Dead:
			lc::notify::Queue("They can't hear you any more.");
			return;
		default:
			lc::notify::Queue("You can only talk to people.");
			return;
		}

		if (sheet.GetBool("interior") && !g_lastOutdoorPlace.empty())
			sheet.Set("last_outdoor_place", g_lastOutdoorPlace);
		if (sheet.GetBool("interior") && !g_lastOutdoorWorld.empty())
			sheet.Set("last_outdoor_x", static_cast<double>(g_lastOutdoorX)).Set("last_outdoor_y", static_cast<double>(g_lastOutdoorY))
				.Set("last_outdoor_world_id", g_lastOutdoorWorld);
		LC_MSG("Character sheet: %s", sheet.Dump().c_str());
		g_talking = true;
		g_talkRefId = sheet.GetString("ref_id");
		g_talkStartMs = GetTickCount64();

		lc::notify::Queue("Talking to " + name + ".");
		if (!g_link.IsConnected() || !g_handshakeDone)
		{
			lc::notify::Queue("Living Cyrodiil: the companion isn't running.");
			return;
		}
		EnsurePlaythrough();
		lc::json::Value msg;
		msg.Set("type", "talk_start").Set("npc", std::move(sheet));
		g_link.Send(msg.Dump());
		lc::hold::Begin(g_talkRefId);
	}

	// cancelled = the key wasn't released normally (a menu opened, a load began),
	// so the companion throws the recording away instead of answering it.
	void OnTalkKeyUp(bool cancelled, const char* reason = "")
	{
		if (!g_talking)
			return;
		g_talking = false;
		const ULONGLONG held = GetTickCount64() - g_talkStartMs;
		LC_MSG("Talk key %s after %llu ms%s%s", cancelled ? "cancelled" : "up", held,
			*reason ? ": " : "", reason);
		lc::hold::KeyReleased(held, cancelled);
		if (g_link.IsConnected() && g_handshakeDone)
		{
			lc::json::Value msg;
			if (cancelled)
				msg.Set("type", "talk_cancel").Set("ref_id", g_talkRefId).Set("reason", reason);
			else
				msg.Set("type", "talk_end").Set("ref_id", g_talkRefId).Set("held_ms", static_cast<double>(held));
			g_link.Send(msg.Dump());
		}
	}

	// While the player is in the game world, the talk key is hidden from the game
	// (xOBSE input mask), so whatever else it's bound to doesn't fire. Our own
	// check reads the real keyboard and still sees it. In menus and the console
	// the key is unblocked again, so typing it still works.
	bool g_keyBlocked = false;

	void SetTalkKeyBlocked(bool block)
	{
		if (!g_settings.blockTalkKey || block == g_keyBlocked || !g_input)
			return;
		if (block && g_input->DisableInputKey)
			g_input->DisableInputKey(g_settings.talkKey);
		else if (!block && g_input->EnableInputKey)
			g_input->EnableInputKey(g_settings.talkKey);
		g_keyBlocked = block;
		LC_MSG("Talk key %s for the game", block ? "blocked" : "unblocked");
	}

	void PollTalkKey(bool settled)
	{
		if (!g_input || !g_input->IsKeyPressedReal)
			return;

		// In the game world, or in the vanilla dialogue window (which is closed
		// as the talk starts). Not in other menus, the console, or a load screen.
		InterfaceManager* ui = InterfaceManager::GetSingleton();
		const bool console = IsConsoleOpen();
		const bool gameMode = settled && ui && ui->IsGameMode() && !console;
		const bool inDialog = settled && ui && !gameMode && !console && DialogIsActiveMenu();
		const bool closingDialog = g_talking && GetTickCount64() < g_closingDialogUntil;
		SetTalkKeyBlocked(gameMode);
		const bool pressed = g_input->IsKeyPressedReal(g_settings.talkKey);
		const bool pressedNow = pressed && !g_rawKeyWasDown;    // a fresh press, this frame
		g_rawKeyWasDown = pressed;

		if (!g_talking)
		{
			if (pressedNow && gameMode)
				OnTalkKeyDown(false);
			else if (pressedNow && inDialog)
				OnTalkKeyDown(true);
		}
		else if (!pressed)
		{
			OnTalkKeyUp(false);
		}
		else if (!gameMode && !closingDialog)
		{
			// A menu or the console opened mid-talk (not the dialogue window we're closing).
			OnTalkKeyUp(true, console ? "console opened" : "menu opened");
		}
		g_keyWasDown = g_talking;
	}

	// ---- typed input (Phase 5) ---------------------------------------------------
	// The type key opens the game's text box for the NPC under the crosshair;
	// what's typed goes to the companion as talk_text and is answered like a
	// spoken line. The NPC is held from the moment the box opens.

	bool g_typeKeyWasDown = false;
	std::string g_typeRefId;
	lc::json::Value g_typeSheet;
	bool g_typeKeyBlocked = false;

	void SetTypeKeyBlocked(bool block)
	{
		if (!g_settings.blockTalkKey || !g_settings.typeKey || block == g_typeKeyBlocked || !g_input)
			return;
		if (block && g_input->DisableInputKey)
			g_input->DisableInputKey(g_settings.typeKey);
		else if (!block && g_input->EnableInputKey)
			g_input->EnableInputKey(g_settings.typeKey);
		g_typeKeyBlocked = block;
		LC_MSG("Type key %s for the game", block ? "blocked" : "unblocked");
	}

	void PollTypeKey(bool settled)
	{
		if (!g_settings.typeKey || !g_input || !g_input->IsKeyPressedReal)
			return;
		std::string typed;
		bool sent = false;
		if (lc::typebox::Tick(&typed, &sent))
		{
			if (sent && !typed.empty() && g_link.IsConnected() && g_handshakeDone)
			{
				LC_MSG("Typed to %s: \"%s\"", g_typeRefId.c_str(), typed.c_str());
				lc::json::Value msg;
				msg.Set("type", "talk_text").Set("ref_id", g_typeRefId).Set("npc", g_typeSheet).Set("text", typed);
				g_link.Send(msg.Dump());
				lc::hold::KeyReleased(1000, false);      // held until the reply comes and ends
			}
			else
			{
				if (sent && !typed.empty())
					lc::notify::Queue("Living Cyrodiil: the companion isn't running.");
				LC_MSG("Text box closed without sending");
				lc::hold::KeyReleased(0, true);          // let them go at once
			}
		}
		const bool pressed = g_input->IsKeyPressedReal(g_settings.typeKey);
		const bool pressedNow = pressed && !g_typeKeyWasDown;
		g_typeKeyWasDown = pressed;
		InterfaceManager* ui = InterfaceManager::GetSingleton();
		const bool gameMode = settled && ui && ui->IsGameMode() && !IsConsoleOpen();
		SetTypeKeyBlocked(gameMode);
		if (!pressedNow || !gameMode || g_talking || lc::typebox::IsOpen())
			return;
		lc::npc::Target target = lc::npc::Target::Nothing;
		lc::json::Value sheet;
		std::string name;
		if (!SafeReadTarget(false, &target, &sheet, &name))
			return;
		LC_MSG("Type key down: crosshair is %s", lc::npc::TargetText(target));
		switch (target)
		{
		case lc::npc::Target::Ok:
			break;
		case lc::npc::Target::Nothing:
			lc::notify::Queue("There's nobody there to talk to.");
			return;
		case lc::npc::Target::Creature:
			lc::notify::Queue("You can't talk to that creature.");
			return;
		case lc::npc::Target::Dead:
			lc::notify::Queue("They can't hear you any more.");
			return;
		default:
			lc::notify::Queue("You can only talk to people.");
			return;
		}
		if (!g_link.IsConnected() || !g_handshakeDone)
		{
			lc::notify::Queue("Living Cyrodiil: the companion isn't running.");
			return;
		}
		if (sheet.GetBool("interior") && !g_lastOutdoorPlace.empty())
			sheet.Set("last_outdoor_place", g_lastOutdoorPlace);
		if (sheet.GetBool("interior") && !g_lastOutdoorWorld.empty())
			sheet.Set("last_outdoor_x", static_cast<double>(g_lastOutdoorX)).Set("last_outdoor_y", static_cast<double>(g_lastOutdoorY))
				.Set("last_outdoor_world_id", g_lastOutdoorWorld);
		g_typeRefId = sheet.GetString("ref_id");
		g_typeSheet = sheet;
		LC_MSG("Character sheet: %s", sheet.Dump().c_str());
		// The companion gets the NPC's voice ready (and the LLM connection)
		// while the player types.
		EnsurePlaythrough();
		lc::json::Value msg;
		msg.Set("type", "type_start").Set("npc", std::move(sheet));
		g_link.Send(msg.Dump());
		lc::hold::Begin(g_typeRefId);
		lc::typebox::Open("Say to " + name + ":");
	}

	// ---- per-frame tick (main thread) -------------------------------------------

	void TickImpl()
	{
		if (!g_loggedFirstTick)
		{
			LC_MSG("Main-thread tick is running");
			g_loggedFirstTick = true;
		}

		std::vector<lc::LinkEvent> events;
		g_link.Poll(events);
		for (auto& ev : events)
		{
			switch (ev.kind)
			{
			case lc::LinkEvent::Kind::Connected:
				LC_MSG("Connected to companion; waiting for its hello");
				break;
			case lc::LinkEvent::Kind::Disconnected:
				LC_MSG("Companion disconnected");
				if (g_handshakeDone && g_settings.showConnection)
					lc::notify::Queue("Living Cyrodiil: companion disconnected.");
				g_handshakeDone = false;
				break;
			case lc::LinkEvent::Kind::Line:
				HandleCompanionLine(ev.line);
				break;
			}
		}

		bool settled = false;
		if (g_inGame)
		{
			ULONGLONG now = GetTickCount64();
			if (g_inGameSinceMs == 0)
				g_inGameSinceMs = now;
			settled = now - g_inGameSinceMs >= kSettleMs;
		}
		if (settled && GetTickCount64() >= g_nextPlaceCheckMs)
		{
			g_nextPlaceCheckMs = GetTickCount64() + 1000;     // cheap, but no need every frame
			// Outdoors, the place is the world's name: a walled city's ("Bravil"),
			// or "" in the open country, whose world has no name. "" must replace
			// the old name too: MVXK21 walked from Leyawiin to the Bravil docks and
			// an inn there was "in Leyawiin" (Nov 11 playtest of 0.21.2). With ""
			// the companion names the place from the position instead.
			if (lc::npc::PlayerOutdoorPos(&g_lastOutdoorX, &g_lastOutdoorY, &g_lastOutdoorWorld))
			{
				std::string place = lc::npc::PlayerOutdoorPlace();
				if (place != g_lastOutdoorPlace)
				{
					g_lastOutdoorPlace = place;
					LC_MSG("Player is outdoors in %s", place.empty() ? "the open country (no world name; named from the position)" : place.c_str());
				}
			}
		}
		PollTalkKey(settled);
		PollTypeKey(settled);
		lc::speak::Tick();
		lc::hold::Tick();
		if (settled)
		{
			lc::vanilla::Tick();
			lc::chatter::Tick();
		}
		lc::notify::ShowPending(settled);
	}

	void Tick()
	{
		__try
		{
			TickImpl();
		}
		__except (EXCEPTION_EXECUTE_HANDLER)
		{
			LC_ERR("Crash avoided in per-frame tick (exception %08X). Please report this with the log.", GetExceptionCode());
		}
	}

	// ---- xOBSE events (main thread) ------------------------------------------------

	const char* MessageName(UInt32 type)
	{
		switch (type)
		{
		case OBSEMessagingInterface::kMessage_PostLoad:          return "PostLoad (all plugins loaded)";
		case OBSEMessagingInterface::kMessage_PostPostLoad:      return "PostPostLoad";
		case OBSEMessagingInterface::kMessage_GameInitialized:   return "GameInitialized (main menu shown)";
		case OBSEMessagingInterface::kMessage_PreLoadGame:       return "PreLoadGame";
		case OBSEMessagingInterface::kMessage_LoadGame:          return "LoadGame";
		case OBSEMessagingInterface::kMessage_PostLoadGame:      return "PostLoadGame";
		case OBSEMessagingInterface::kMessage_SaveGame:          return "SaveGame";
		case OBSEMessagingInterface::kMessage_ExitToMainMenu:    return "ExitToMainMenu";
		case OBSEMessagingInterface::kMessage_ExitGame:          return "ExitGame";
		case OBSEMessagingInterface::kMessage_ExitGame_Console:  return "ExitGame (qqq console)";
		case OBSEMessagingInterface::kMessage_RuntimeScriptError:return "RuntimeScriptError";
		default:                                                 return "unknown";
		}
	}

	void HandleOBSEMessage(OBSEMessagingInterface::Message* msg)
	{
		if (!msg)
			return;

		switch (msg->type)
		{
		case OBSEMessagingInterface::kMessage_PreLoadGame:
			g_inGame = false;
			lc::playthrough::BeforeLoad();
			lc::speak::Stop();
			lc::hold::Forget();
			lc::chatter::Forget();
			g_lastOutdoorPlace.clear();     // belongs to the previous save
			g_lastOutdoorWorld.clear();
			OnTalkKeyUp(true, "loading a save");
			g_keyWasDown = false;
			SetTalkKeyBlocked(false);
			SetTypeKeyBlocked(false);
			LC_MSG("Event: %s, file: %s", MessageName(msg->type),
				msg->data ? static_cast<const char*>(msg->data) : "(none)");
			break;

		case OBSEMessagingInterface::kMessage_LoadGame:
			g_lastSaveName = FileNameOnly(static_cast<const char*>(msg->data));
			LC_MSG("Event: %s, file: %s", MessageName(msg->type), g_lastSaveName.c_str());
			lc::playthrough::OnLoad(g_lastSaveName);
			break;

		case OBSEMessagingInterface::kMessage_PostLoadGame:
		{
			// xOBSE passes the success flag AS the data value ((void*)bool), not
			// a pointer to it. Never dereference it (that crashed v0.1.0).
			const bool ok = reinterpret_cast<uintptr_t>(msg->data) != 0;
			LC_MSG("Event: %s, success: %s", MessageName(msg->type), ok ? "yes" : "no");
			if (ok)
			{
				g_inGame = true;
				g_inGameSinceMs = 0;
				lc::hold::AfterLoad();
				lc::playthrough::AfterLoad();
				SendEvent("save_loaded", g_lastSaveName);
				SendPlaythrough();
				if (!g_settings.warning.empty())
				{
					lc::notify::Queue(g_settings.warning);     // once per game session
					g_settings.warning.clear();
				}
				if (g_settings.showGreeting)
				{
					char line[96];
					sprintf_s(line, "Living Cyrodiil %s is loaded.", LC_VERSION_STRING);
					Console_Print("%s", line);
					lc::notify::Queue(line);
				}
			}
			break;
		}

		case OBSEMessagingInterface::kMessage_SaveGame:
			LC_MSG("Event: %s, file: %s", MessageName(msg->type),
				msg->data ? static_cast<const char*>(msg->data) : "(none)");
			SendEvent("save_saved", FileNameOnly(static_cast<const char*>(msg->data)));
			if (msg->data)
				lc::playthrough::OnSave(static_cast<const char*>(msg->data));
			break;

		case OBSEMessagingInterface::kMessage_ExitToMainMenu:
			g_inGame = false;
			lc::speak::Stop();
			lc::hold::Forget();
			lc::chatter::Forget();
			LC_MSG("Event: %s", MessageName(msg->type));
			lc::playthrough::Reset();
			OnTalkKeyUp(true, "main menu");
			g_keyWasDown = false;
			SetTalkKeyBlocked(false);
			SetTypeKeyBlocked(false);
			SendEvent("main_menu");
			break;

		case OBSEMessagingInterface::kMessage_ExitGame:
		case OBSEMessagingInterface::kMessage_ExitGame_Console:
			g_inGame = false;
			lc::speak::Stop();
			lc::hold::Forget();
			lc::chatter::Forget();
			LC_MSG("Event: %s", MessageName(msg->type));
			OnTalkKeyUp(true, "game closing");
			SetTalkKeyBlocked(false);
			SetTypeKeyBlocked(false);
			SendEvent("game_exit");
			g_link.Stop();       // sends what's queued, then closes
			break;

		case OBSEMessagingInterface::kMessage_GameInitialized:
			LC_MSG("Event: %s", MessageName(msg->type));
			break;

		case OBSEMessagingInterface::kMessage_RuntimeScriptError:
			// Other mods' script errors. Not ours, but handy context in reports.
			LC_MSG("Event: %s: %s", MessageName(msg->type),
				msg->data ? static_cast<const char*>(msg->data) : "(no text)");
			break;

		default:
			LC_MSG("Event: %s (type %u)", MessageName(msg->type), msg->type);
			break;
		}
	}

	// Safety net: a bug in our handler must never take the game down.
	void OnOBSEMessage(OBSEMessagingInterface::Message* msg)
	{
		__try
		{
			HandleOBSEMessage(msg);
		}
		__except (EXCEPTION_EXECUTE_HANDLER)
		{
			LC_ERR("Crash avoided while handling xOBSE event type %u (exception %08X). Please report this with the log.",
				msg ? msg->type : 0xFFFFFFFF, GetExceptionCode());
		}
	}
}

extern "C" {

// Called first. Decide whether we can run in this game; do no real work here.
bool OBSEPlugin_Query(const OBSEInterface* obse, PluginInfo* info)
{
	OpenLog();

	info->infoVersion = PluginInfo::kInfoVersion;
	info->name = "LivingCyrodiil";
	info->version = LC_VERSION_INTEGER;

	if (obse->isEditor)
	{
		LC_MSG("Construction Set detected; Living Cyrodiil only runs in the game. Not loading.");
		return false;
	}

	LC_MSG("xOBSE version %u, Oblivion version %08X", obse->obseVersion, obse->oblivionVersion);

	if (obse->obseVersion < OBSE_VERSION_INTEGER)
	{
		LC_ERR("xOBSE is too old (found %u, need at least %u). Please update xOBSE.", obse->obseVersion, OBSE_VERSION_INTEGER);
		return false;
	}

	if (obse->oblivionVersion != OBLIVION_VERSION)
	{
		LC_ERR("Wrong Oblivion version (found %08X, need %08X = patch 1.2.0416).", obse->oblivionVersion, OBLIVION_VERSION);
		return false;
	}

	LC_MSG("Version checks passed");
	return true;
}

// Called once if Query returned true. Set up listeners; never block here.
bool OBSEPlugin_Load(const OBSEInterface* obse)
{
	g_pluginHandle = obse->GetPluginHandle();
	LC_MSG("Load: plugin handle %u", g_pluginHandle);

	GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
		reinterpret_cast<LPCSTR>(&OBSEPlugin_Load), &g_module);
	g_settings = lc::LoadSettings(g_module);
	// Start the bundled companion now, so it loads its models while the game loads.
	if (g_settings.autoStartCompanion)
	{
		std::string problem = lc::launch::Start(g_module, g_settings.companionPort);
		if (!problem.empty())
			g_settings.warning = g_settings.warning.empty() ? problem : g_settings.warning + " " + problem;
	}
	else
		LC_MSG("Companion launch: off (AutoStart=0)");

	g_messaging = static_cast<OBSEMessagingInterface*>(obse->QueryInterface(kInterface_Messaging));
	if (!g_messaging || !g_messaging->RegisterListener(g_pluginHandle, "OBSE", OnOBSEMessage))
	{
		LC_ERR("Couldn't register for xOBSE messages; can't hear game events");
		return false;
	}
	LC_MSG("Listening for xOBSE events");

	auto* console = static_cast<OBSEConsoleInterface*>(obse->QueryInterface(kInterface_Console));
	if (!console)
		LC_WARN("Console interface not available; no on-screen notifications");
	lc::notify::Init(console);
	lc::speak::Init(console);
	lc::hold::Init(console);
	{
		auto* commands = static_cast<OBSECommandTableInterface*>(obse->QueryInterface(kInterface_CommandTable));
		lc::chatter::Init(commands);
		lc::awareness::Init(commands);
		lc::quests::Init(commands);
	}
	lc::hold::SetReleaseHandler([](const std::string& ref, const char* why) {
		if (!g_link.IsConnected() || !g_handshakeDone)
			return;
		lc::json::Value msg;
		msg.Set("type", "talk_over").Set("ref_id", ref).Set("reason", why ? why : "");
		g_link.Send(msg.Dump());
	});
	lc::vanilla::SetHandler([](const std::string& ref, const std::string& name, const std::string& topic,
			const std::string& text) {
		if (!g_link.IsConnected() || !g_handshakeDone)
			return;
		lc::json::Value msg;
		msg.Set("type", "vanilla_line").Set("ref_id", ref).Set("name", name).Set("text", text);
		if (!topic.empty())
			msg.Set("topic", topic);
		g_link.Send(msg.Dump());
	});
	if (g_settings.logVoiceFiles)
		lc::filewatch::Install();      // diagnostic: logs which voice files the game looks for
	lc::chatter::SetTracking(g_settings.chatterAwareness);

	g_tasks = static_cast<OBSETasks2Interface*>(obse->QueryInterface(kInterface_Tasks2));
	if (g_tasks && g_tasks->EnqueueTask)
	{
		g_tasks->EnqueueTask(Tick);   // stays queued; runs every frame
		LC_MSG("Per-frame tick registered");
	}
	else
	{
		LC_ERR("Task interface not available; can't run per-frame code. Living Cyrodiil is inactive.");
		return true;   // stay loaded harmlessly
	}

	g_input = static_cast<OBSEInputInterface*>(obse->QueryInterface(kInterface_Input));
	if (!g_input)
		LC_WARN("Input interface not available; push-to-talk disabled");

	g_link.Start(g_settings.companionPort, HelloLine());

	LC_MSG("Load finished");
	return true;
}

}
