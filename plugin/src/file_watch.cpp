#include "file_watch.h"
#include "log.h"

#include <cctype>
#include <cstring>

namespace lc::filewatch
{
	namespace
	{
		const long kMaxLines = 400;
		volatile long g_lines = 0;

		using CreateFileA_t = HANDLE(WINAPI*)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
		using GetFileAttributesA_t = DWORD(WINAPI*)(LPCSTR);
		using GetFileAttributesExA_t = BOOL(WINAPI*)(LPCSTR, GET_FILEEX_INFO_LEVELS, LPVOID);
		using FindFirstFileA_t = HANDLE(WINAPI*)(LPCSTR, LPWIN32_FIND_DATAA);

		CreateFileA_t          g_createFile = nullptr;
		GetFileAttributesA_t   g_getAttr = nullptr;
		GetFileAttributesExA_t g_getAttrEx = nullptr;
		FindFirstFileA_t       g_findFirst = nullptr;

		bool Interesting(const char* path)
		{
			if (!path)
				return false;
			// case-insensitive search for "voice"
			for (const char* p = path; *p; ++p)
			{
				const char* a = p;
				const char* b = "voice";
				while (*a && *b && tolower(static_cast<unsigned char>(*a)) == *b) { ++a; ++b; }
				if (!*b)
					return true;
			}
			return false;
		}

		void Note(const char* fn, const char* path, bool found)
		{
			if (!Interesting(path))
				return;
			const long n = InterlockedIncrement(&g_lines);
			if (n <= kMaxLines)
				LC_MSG("File check: %s %s -> %s", fn, path, found ? "found" : "missing");
			else if (n == kMaxLines + 1)
				LC_MSG("File check: line limit reached, not logging more");
		}

		HANDLE WINAPI HookCreateFileA(LPCSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
			DWORD disposition, DWORD flags, HANDLE templ)
		{
			HANDLE h = g_createFile(name, access, share, sa, disposition, flags, templ);
			const DWORD err = GetLastError();
			Note("open", name, h != INVALID_HANDLE_VALUE);
			SetLastError(err);
			return h;
		}

		DWORD WINAPI HookGetFileAttributesA(LPCSTR name)
		{
			DWORD r = g_getAttr(name);
			const DWORD err = GetLastError();
			Note("attributes", name, r != INVALID_FILE_ATTRIBUTES);
			SetLastError(err);
			return r;
		}

		BOOL WINAPI HookGetFileAttributesExA(LPCSTR name, GET_FILEEX_INFO_LEVELS level, LPVOID info)
		{
			BOOL r = g_getAttrEx(name, level, info);
			const DWORD err = GetLastError();
			Note("attributes", name, r != FALSE);
			SetLastError(err);
			return r;
		}

		HANDLE WINAPI HookFindFirstFileA(LPCSTR name, LPWIN32_FIND_DATAA data)
		{
			HANDLE h = g_findFirst(name, data);
			const DWORD err = GetLastError();
			Note("find", name, h != INVALID_HANDLE_VALUE);
			SetLastError(err);
			return h;
		}

		// Replaces one import of the game exe. Matches by name where the import
		// names are available, otherwise by the function's current address.
		// Stores the previous entry (what we chain to) in *original before
		// switching the slot, so the hook never runs without it.
		bool PatchImport(HMODULE exe, const char* fn, void* hook, void** original)
		{
			HMODULE k32 = GetModuleHandleA("kernel32.dll");
			void* real = k32 ? reinterpret_cast<void*>(GetProcAddress(k32, fn)) : nullptr;

			auto* base = reinterpret_cast<BYTE*>(exe);
			auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
			auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
			const auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
			if (!dir.VirtualAddress)
				return false;

			for (auto* imp = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress); imp->Name; ++imp)
			{
				auto* slot = reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->FirstThunk);
				auto* names = imp->OriginalFirstThunk
					? reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->OriginalFirstThunk) : nullptr;
				for (int i = 0; slot[i].u1.Function; ++i)
				{
					bool match = false;
					if (names && !(names[i].u1.Ordinal & IMAGE_ORDINAL_FLAG))
					{
						auto* byName = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base + names[i].u1.AddressOfData);
						match = strcmp(reinterpret_cast<const char*>(byName->Name), fn) == 0;
					}
					else if (real)
					{
						match = reinterpret_cast<void*>(slot[i].u1.Function) == real;
					}
					if (!match)
						continue;

					*original = reinterpret_cast<void*>(slot[i].u1.Function);
					DWORD old = 0;
					if (!VirtualProtect(&slot[i].u1.Function, sizeof(void*), PAGE_READWRITE, &old))
						return false;
					InterlockedExchange(reinterpret_cast<volatile LONG*>(&slot[i].u1.Function),
						static_cast<LONG>(reinterpret_cast<ULONG_PTR>(hook)));
					VirtualProtect(&slot[i].u1.Function, sizeof(void*), old, &old);
					return true;
				}
			}
			return false;
		}

		template <typename T>
		void Hook(HMODULE exe, const char* fn, void* hook, T* original)
		{
			if (PatchImport(exe, fn, hook, reinterpret_cast<void**>(original)))
			{
				LC_MSG("File watch: watching %s", fn);
			}
			else
			{
				LC_MSG("File watch: %s isn't imported by the game exe (not watched)", fn);
			}
		}
	}

	void Install()
	{
		HMODULE exe = GetModuleHandleA(nullptr);
		if (!exe)
			return;
		__try
		{
			Hook(exe, "CreateFileA", reinterpret_cast<void*>(&HookCreateFileA), &g_createFile);
			Hook(exe, "GetFileAttributesA", reinterpret_cast<void*>(&HookGetFileAttributesA), &g_getAttr);
			Hook(exe, "GetFileAttributesExA", reinterpret_cast<void*>(&HookGetFileAttributesExA), &g_getAttrEx);
			Hook(exe, "FindFirstFileA", reinterpret_cast<void*>(&HookFindFirstFileA), &g_findFirst);
		}
		__except (EXCEPTION_EXECUTE_HANDLER)
		{
			LC_ERR("File watch: crash avoided while installing (exception %08X); not watching", GetExceptionCode());
		}
	}
}
