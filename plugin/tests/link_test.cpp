// Standalone test for the plugin's JSON code and companion connection.
// Built and run by plugin\tests\run_tests.ps1, outside the game, against the
// real Python companion code (companion\tests\link_peer.py).
//
// Exit code 0 = all checks passed.

#include <winsock2.h>
#include <windows.h>

#include <cstdio>
#include <cstdarg>
#include <string>
#include <vector>

#include "../src/companion_link.h"
#include "../src/json.h"
#include "../src/key_names.h"
#include "../src/log.h"

// Test stand-in for log.cpp (which writes through xOBSE's logger).
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

static int g_failures = 0;
#define CHECK(cond) do { if (cond) printf("PASS  %s\n", #cond); else { printf("FAIL  %s  (line %d)\n", #cond, __LINE__); ++g_failures; } } while (0)

static void JsonTests()
{
	using lc::json::Value;
	std::string err;

	Value v;
	const std::string sample = R"({"type":"notify","text":"Quote \" and ü and 😀","n":-1.5e2,"ok":true,"list":[1,2,{"x":null}]})";
	const bool parsed = lc::json::Parse(sample, v, err);
	CHECK(parsed);
	CHECK(v.GetString("type") == "notify");
	const std::string expectedText = "Quote \" and \xC3\xBC and \xF0\x9F\x98\x80";   // UTF-8
	const bool unicodeDecoded = v.GetString("text") == expectedText;
	CHECK(unicodeDecoded);
	CHECK(v.GetNumber("n") == -150.0);
	CHECK(v.GetBool("ok") == true);
	CHECK(v.Find("list") && v.Find("list")->AsArray().size() == 3);
	CHECK(v.GetString("missing", "fallback") == "fallback");
	CHECK(v.GetNumber("text", 7) == 7);                   // wrong type -> fallback

	Value bad;
	CHECK(!lc::json::Parse("{\"type\": ", bad, err));
	CHECK(!lc::json::Parse("not json", bad, err));
	CHECK(!lc::json::Parse("{\"a\":1} trailing", bad, err));
	CHECK(!lc::json::Parse(std::string(100, '['), bad, err));   // too deep, no stack overflow

	Value out;
	out.Set("type", "event").Set("name", "save_loaded").Set("n", 3).Set("text", "line\nbreak \"q\"");
	std::string dumped = out.Dump();
	const std::string expectedDump = R"({"type":"event","name":"save_loaded","n":3,"text":"line\nbreak \"q\""})";
	const bool dumpMatches = dumped == expectedDump;
	CHECK(dumpMatches);
	Value back;
	const std::string expectedBack = "line\nbreak \"q\"";
	const bool roundTrips = lc::json::Parse(dumped, back, err) && back.GetString("text") == expectedBack;
	CHECK(roundTrips);
}

// Events arrive in batches; keep every event and a read position, so one
// batch can satisfy several WaitFor calls in a row.
static size_t g_cursor = 0;

static bool WaitFor(lc::CompanionLink& link, std::vector<lc::LinkEvent>& got,
                    lc::LinkEvent::Kind kind, const char* contains, DWORD timeoutMs)
{
	DWORD start = GetTickCount();
	while (true)
	{
		while (g_cursor < got.size())
		{
			const auto& e = got[g_cursor++];
			if (e.kind == kind && (!contains || e.line.find(contains) != std::string::npos))
				return true;
		}
		if (GetTickCount() - start >= timeoutMs)
			return false;
		link.Poll(got);
		Sleep(20);
	}
}

static void LinkTests(unsigned short port)
{
	lc::CompanionLink link;
	std::vector<lc::LinkEvent> got;
	link.Start(port, R"({"type":"hello","protocol":1,"side":"plugin","plugin_version":"test","game":"oblivion"})");

	CHECK(WaitFor(link, got, lc::LinkEvent::Kind::Connected, nullptr, 8000));
	CHECK(WaitFor(link, got, lc::LinkEvent::Kind::Line, "\"side\":\"companion\"", 3000));   // companion's hello
	CHECK(link.IsConnected());

	link.Send(R"({"type":"event","name":"ping"})");
	CHECK(WaitFor(link, got, lc::LinkEvent::Kind::Line, "pong", 3000));                       // round trip

	link.Send(R"({"type":"event","name":"please_disconnect"})");
	CHECK(WaitFor(link, got, lc::LinkEvent::Kind::Disconnected, nullptr, 3000));
	CHECK(!link.IsConnected());

	// The peer accepts again after closing: the link should reconnect by itself.
	CHECK(WaitFor(link, got, lc::LinkEvent::Kind::Connected, nullptr, 8000));
	CHECK(WaitFor(link, got, lc::LinkEvent::Kind::Line, "\"side\":\"companion\"", 3000));

	link.Send(R"({"type":"event","name":"game_exit"})");
	link.Stop();          // must flush game_exit before closing
	CHECK(!link.IsConnected());
}

static void KeyNameTests()
{
	using lc::keys::Parse;
	CHECK(Parse("V") == 47);
	CHECK(Parse("v") == 47);
	CHECK(Parse("F4") == 62);
	CHECK(Parse("left alt") == 56);
	CHECK(Parse("Left_Alt") == 56);
	CHECK(Parse("Numpad0") == 82);
	CHECK(Parse("Mouse3") == 258);
	CHECK(Parse("4") == 5);            // the number key 4, not scan code 4
	CHECK(Parse("47") == 47);          // old-style scan code still works
	CHECK(Parse("F44") == 0);
	CHECK(Parse("") == 0);
	CHECK(Parse("999") == 0);
	CHECK(lc::keys::Name(47) == "V");
	CHECK(lc::keys::Name(258) == "Mouse3");
}

int main(int argc, char** argv)
{
	printf("JSON tests\n");
	JsonTests();

	printf("\nKey name tests\n");
	KeyNameTests();

	if (argc > 1)
	{
		printf("\nConnection tests (companion on port %s)\n", argv[1]);
		LinkTests(static_cast<unsigned short>(atoi(argv[1])));
	}

	printf("\n%s: %d failure(s)\n", g_failures ? "FAILED" : "ALL PASSED", g_failures);
	return g_failures ? 1 : 0;
}
