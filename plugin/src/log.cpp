#include "log.h"

#include <cstdio>
#include <mutex>

namespace lc
{
	namespace
	{
		std::mutex g_logLock;
	}

	void Log(LogLevel level, const char* fmt, ...)
	{
		char buf[2048];
		va_list args;
		va_start(args, fmt);
		vsnprintf(buf, sizeof(buf), fmt, args);   // truncates long lines safely
		va_end(args);

		const char* prefix = level == LogLevel::Error ? "ERROR: " : level == LogLevel::Warning ? "WARNING: " : "";

		std::lock_guard<std::mutex> lock(g_logLock);
		// Pass through "%s" so text containing '%' is never treated as a format.
		IDebugLog::FormattedMessage("%s%s", prefix, buf);
	}
}
