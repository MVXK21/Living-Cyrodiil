// Tests lc::launch against a real release folder (tools\make_release.py's
// output), outside the game. Run by run_launch_test.ps1, which copies this
// exe into <release>\OBSE\Plugins\ so it stands where the plugin DLL does,
// and sets LC_USER_DIR so the companion writes into a scratch folder.
//
//   launch_test.exe <port>    -> starts the bundled companion, waits for it to
//                                listen, then exits (the job object ends it)
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <string>

#include "../src/companion_launch.h"
#include "../src/log.h"

namespace lc
{
	void Log(LogLevel level, const char* fmt, ...)
	{
		va_list args;
		va_start(args, fmt);
		printf("  [plugin%s] ", level == LogLevel::Error ? " ERROR" : level == LogLevel::Warning ? " WARN" : "");
		vprintf(fmt, args);
		printf("\n");
		va_end(args);
	}
}

static bool Listening(unsigned short port)
{
	SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	sockaddr_in a = {};
	a.sin_family = AF_INET;
	a.sin_port = htons(port);
	inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
	bool ok = connect(s, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == 0;
	closesocket(s);
	return ok;
}

int main(int argc, char** argv)
{
	unsigned short port = static_cast<unsigned short>(argc > 1 ? atoi(argv[1]) : 47833);
	WSADATA wsa;
	WSAStartup(MAKEWORD(2, 2), &wsa);
	std::string problem = lc::launch::Start(nullptr, port);
	printf("Start returned: \"%s\"\n", problem.c_str());
	// The first start loads big libraries: allow a couple of minutes.
	for (int i = 0; i < 240; ++i)
	{
		if (Listening(port))
		{
			printf("PASS  the companion listens on port %u after %.1f s\n", port, i * 0.5);
			// A second start must not launch another companion.
			std::string again = lc::launch::Start(nullptr, port);
			printf("Second Start returned: \"%s\"\n", again.c_str());
			return 0;
		}
		Sleep(500);
	}
	printf("FAIL  the companion never listened on port %u\n", port);
	return 1;
}
