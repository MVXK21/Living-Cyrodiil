#pragma once

// Thread-safe logging to LivingCyrodiil.log. xOBSE's IDebugLog uses shared
// static buffers, so calls from the network thread and the game thread must
// not overlap. Use these instead of _MESSAGE/_WARNING/_ERROR in our code.

#include <cstdarg>

namespace lc
{
	enum class LogLevel { Message, Warning, Error };
	void Log(LogLevel level, const char* fmt, ...);
}

#define LC_MSG(...)  ::lc::Log(::lc::LogLevel::Message, __VA_ARGS__)
#define LC_WARN(...) ::lc::Log(::lc::LogLevel::Warning, __VA_ARGS__)
#define LC_ERR(...)  ::lc::Log(::lc::LogLevel::Error, __VA_ARGS__)
