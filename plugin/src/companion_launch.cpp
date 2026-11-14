#include "companion_launch.h"
#include "log.h"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shlobj.h>

#include <string>
#include <vector>

#pragma comment(lib, "ws2_32.lib")

namespace lc::launch
{
	namespace
	{
		const wchar_t* const kCompanionFolder = L"LCCompanion";
		// The longest file the companion loads is about 110 characters inside
		// its folder, so past this the 260-character limit can break it.
		const size_t kMaxFolderChars = 140;

		HANDLE g_job = nullptr;      // kept open for the game's lifetime: closing it ends the companion

		std::string Narrow(const std::wstring& w)
		{
			if (w.empty())
				return {};
			int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
			std::string s(n, '\0');
			WideCharToMultiByte(CP_UTF8, 0, w.c_str(), static_cast<int>(w.size()), &s[0], n, nullptr, nullptr);
			return s;
		}

		std::wstring ParentOf(const std::wstring& path)
		{
			size_t cut = path.find_last_of(L"\\/");
			return cut == std::wstring::npos ? std::wstring() : path.substr(0, cut);
		}

		bool Exists(const std::wstring& path)
		{
			return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
		}

		// The file's real path. Under MO2 the DLL is opened through its virtual
		// Data folder; the handle points at the real file in the mod's folder.
		std::wstring RealPath(const std::wstring& path)
		{
			HANDLE h = CreateFileW(path.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
				OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
			if (h == INVALID_HANDLE_VALUE)
				return path;
			std::vector<wchar_t> buf(32768);
			DWORD n = GetFinalPathNameByHandleW(h, buf.data(), static_cast<DWORD>(buf.size()), FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
			CloseHandle(h);
			if (n == 0 || n >= buf.size())
				return path;
			std::wstring real(buf.data(), n);
			if (real.rfind(L"\\\\?\\UNC\\", 0) == 0)
				real = L"\\\\" + real.substr(8);
			else if (real.rfind(L"\\\\?\\", 0) == 0)
				real = real.substr(4);
			return real;
		}

		// Is something already listening on the companion's port? (A companion
		// started by hand, in a development setup.)
		bool PortAnswers(unsigned short port)
		{
			WSADATA wsa;
			if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
				return false;
			bool answers = false;
			SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
			if (s != INVALID_SOCKET)
			{
				u_long nonBlocking = 1;
				ioctlsocket(s, FIONBIO, &nonBlocking);
				sockaddr_in addr = {};
				addr.sin_family = AF_INET;
				addr.sin_port = htons(port);
				inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
				connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
				fd_set writable, failed;
				FD_ZERO(&writable); FD_SET(s, &writable);
				FD_ZERO(&failed); FD_SET(s, &failed);
				timeval wait = { 0, 300 * 1000 };
				if (select(0, nullptr, &writable, &failed, &wait) > 0 && FD_ISSET(s, &writable))
					answers = true;
				closesocket(s);
			}
			WSACleanup();
			return answers;
		}

		// The game's environment for the companion, minus anything that would
		// point the bundled Python at another Python installed on the PC.
		std::vector<wchar_t> Environment(const std::wstring& folder)
		{
			std::vector<std::wstring> vars;
			if (wchar_t* env = GetEnvironmentStringsW())
			{
				for (const wchar_t* p = env; *p; p += wcslen(p) + 1)
				{
					std::wstring v(p);
					std::wstring upper = v;
					for (auto& c : upper) c = towupper(c);
					if (upper.rfind(L"PYTHONPATH=", 0) == 0 || upper.rfind(L"PYTHONHOME=", 0) == 0
						|| upper.rfind(L"PYTHONSTARTUP=", 0) == 0 || upper.rfind(L"VIRTUAL_ENV=", 0) == 0)
						continue;
					vars.push_back(v);
				}
				FreeEnvironmentStringsW(env);
			}
			// The running game's own Data folder, so the companion needn't find
			// it in the registry (GOG, moved or copied installs, MO2's Stock Game).
			{
				std::vector<wchar_t> exe(32768);
				DWORD n = GetModuleFileNameW(nullptr, exe.data(), static_cast<DWORD>(exe.size()));
				if (n && n < exe.size())
					vars.push_back(L"LC_GAME_DATA=" + ParentOf(std::wstring(exe.data(), n)) + L"\\Data");
			}
			// Where the bundled Python and the companion really are. Under MO2,
			// Python finding itself sees the virtual Data folder instead
			// (Nov 14: "Stock Game\data\LCCompanion\python") and couldn't load
			// its own standard library from there: "No module named 'encodings'".
			vars.push_back(L"PYTHONHOME=" + folder + L"\\python");
			vars.push_back(L"PYTHONPATH=" + folder);
			// Its output goes to companion-start.txt: flush every line, and on a
			// crash inside a library, print where (Nov 14: the companion died at
			// once under MO2, before writing its own log).
			vars.push_back(L"PYTHONUNBUFFERED=1");
			vars.push_back(L"PYTHONFAULTHANDLER=1");
			vars.push_back(L"PYTHONNOUSERSITE=1");
			vars.push_back(L"PYTHONUTF8=1");
			vars.push_back(L"PYTHONIOENCODING=utf-8");
			std::vector<wchar_t> block;
			for (const auto& v : vars)
			{
				block.insert(block.end(), v.begin(), v.end());
				block.push_back(L'\0');
			}
			block.push_back(L'\0');
			return block;
		}

		// Documents\My Games\Oblivion\LivingCyrodiil\companion-start.txt, beside
		// the plugin's log: the companion's own output from its start.
		HANDLE OpenOutputFile()
		{
			std::wstring dir;
			wchar_t test[MAX_PATH] = {};
			if (GetEnvironmentVariableW(L"LC_USER_DIR", test, MAX_PATH))   // tests: a scratch folder
				dir = test;
			else
			{
				wchar_t docs[MAX_PATH] = {};
				if (FAILED(SHGetFolderPathW(nullptr, CSIDL_PERSONAL, nullptr, 0, docs)))
					return INVALID_HANDLE_VALUE;
				dir = std::wstring(docs) + L"\\My Games\\Oblivion\\LivingCyrodiil";
			}
			CreateDirectoryW(dir.c_str(), nullptr);
			SECURITY_ATTRIBUTES sa = { sizeof(sa), nullptr, TRUE };     // inherited by the companion
			return CreateFileW((dir + L"\\companion-start.txt").c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
				&sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		}

		struct Watch { HANDLE process; ULONGLONG started; };

		// Logs when the companion ends, and how (it should run until the game closes).
		DWORD WINAPI WatchCompanion(LPVOID param)
		{
			Watch* w = static_cast<Watch*>(param);
			WaitForSingleObject(w->process, INFINITE);
			DWORD code = 0;
			GetExitCodeProcess(w->process, &code);
			const double secs = (GetTickCount64() - w->started) / 1000.0;
			LC_WARN("Companion launch: the companion ended after %.1f s with exit code %lu (0x%08lX); "
				"its output is in companion-start.txt beside this log", secs, code, code);
			CloseHandle(w->process);
			delete w;
			return 0;
		}

		bool Launch(const std::wstring& python, const std::wstring& folder, DWORD* pid)
		{
			// -P: the companion's code comes from PYTHONPATH (its real folder), not
			// the current folder, which MO2 may also report through its virtual Data.
			std::wstring cmd = L"\"" + python + L"\" -P -m living_cyrodiil.main";
			std::vector<wchar_t> cmdBuf(cmd.begin(), cmd.end());
			cmdBuf.push_back(L'\0');
			std::vector<wchar_t> env = Environment(folder);
			HANDLE out = OpenOutputFile();
			// Only the output file is handed down, not every inheritable handle the game has.
			SIZE_T attrSize = 0;
			InitializeProcThreadAttributeList(nullptr, 1, 0, &attrSize);
			std::vector<char> attrBuf(attrSize);
			auto* attrs = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attrBuf.data());
			STARTUPINFOEXW si = {};
			si.StartupInfo.cb = sizeof(si);
			BOOL inherit = FALSE;
			if (out != INVALID_HANDLE_VALUE && InitializeProcThreadAttributeList(attrs, 1, 0, &attrSize)
				&& UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, &out, sizeof(out), nullptr, nullptr))
			{
				si.lpAttributeList = attrs;
				si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
				si.StartupInfo.hStdOutput = out;
				si.StartupInfo.hStdError = out;
				si.StartupInfo.hStdInput = nullptr;
				inherit = TRUE;
			}
			else
				LC_WARN("Companion launch: couldn't open companion-start.txt; its output is lost");
			PROCESS_INFORMATION pi = {};
			DWORD flags = CREATE_NO_WINDOW | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
				| (si.lpAttributeList ? EXTENDED_STARTUPINFO_PRESENT : 0);
			// Out of any job the game is in, so ours can own it; some launchers
			// don't allow that, so try without.
			BOOL ok = CreateProcessW(python.c_str(), cmdBuf.data(), nullptr, nullptr, inherit,
				flags | CREATE_BREAKAWAY_FROM_JOB, env.data(), folder.c_str(), &si.StartupInfo, &pi);
			if (!ok)
				ok = CreateProcessW(python.c_str(), cmdBuf.data(), nullptr, nullptr, inherit,
					flags, env.data(), folder.c_str(), &si.StartupInfo, &pi);
			if (si.lpAttributeList)
				DeleteProcThreadAttributeList(attrs);
			if (out != INVALID_HANDLE_VALUE)
				CloseHandle(out);            // the companion has its own copy
			if (!ok)
			{
				LC_ERR("Companion launch: CreateProcess failed (error %lu)", GetLastError());
				return false;
			}
			g_job = CreateJobObjectW(nullptr, nullptr);
			if (g_job)
			{
				JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {};
				limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
				if (!SetInformationJobObject(g_job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))
					|| !AssignProcessToJobObject(g_job, pi.hProcess))
					LC_WARN("Companion launch: couldn't tie it to the game (error %lu); it may outlive a crash", GetLastError());
			}
			ResumeThread(pi.hThread);
			*pid = pi.dwProcessId;
			CloseHandle(pi.hThread);
			HANDLE t = CreateThread(nullptr, 0, WatchCompanion, new Watch{ pi.hProcess, GetTickCount64() }, 0, nullptr);
			if (t)
				CloseHandle(t);
			else
				CloseHandle(pi.hProcess);
			return true;
		}
	}

	std::string Start(void* pluginModule, unsigned short port)
	{
		std::vector<wchar_t> buf(32768);
		DWORD n = GetModuleFileNameW(static_cast<HMODULE>(pluginModule), buf.data(), static_cast<DWORD>(buf.size()));
		if (n == 0 || n >= buf.size())
		{
			LC_WARN("Companion launch: couldn't find the plugin's own file");
			return {};
		}
		const std::wstring dll(buf.data(), n);
		const std::wstring real = RealPath(dll);
		// <mod>\OBSE\Plugins\LivingCyrodiil.dll -> <mod>
		const std::wstring mod = ParentOf(ParentOf(ParentOf(real)));
		const std::wstring folder = mod + L"\\" + kCompanionFolder;
		const std::wstring python = folder + L"\\python\\python.exe";
		LC_MSG("Companion launch: plugin at %s; looking for the companion in %s", Narrow(real).c_str(), Narrow(folder).c_str());
		if (!Exists(folder + L"\\release.txt") || !Exists(python))
		{
			LC_MSG("Companion launch: no bundled companion there (a development setup?); start it by hand");
			return {};
		}
		if (PortAnswers(port))
		{
			LC_MSG("Companion launch: a companion already answers on port %u; not starting another", port);
			return {};
		}
		std::string warning;
		if (folder.size() > kMaxFolderChars)
		{
			LC_WARN("Companion launch: its folder path is %zu characters long (over %zu); some files may fail to load",
				folder.size(), kMaxFolderChars);
			warning = "Living Cyrodiil: the mod's folder path is very long, which can stop the companion from "
				"starting. If NPCs don't answer, move your mods to a shorter folder (see the mod's README).";
		}
		DWORD pid = 0;
		if (!Launch(python, folder, &pid))
			return "Living Cyrodiil: couldn't start its companion program. See LivingCyrodiil.log (Documents\\My Games\\Oblivion\\LivingCyrodiil).";
		LC_MSG("Companion launch: started (process %lu); it closes with the game", pid);
		return warning;
	}
}
