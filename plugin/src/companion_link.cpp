#include "companion_link.h"
#include "log.h"

#include <winsock2.h>
#include <ws2tcpip.h>

#pragma comment(lib, "ws2_32.lib")

namespace lc
{
	namespace
	{
		const size_t   kMaxLine = 64 * 1024;           // protocol limit
		const size_t   kMaxQueued = 200;               // per direction
		const DWORD    kRetryMs = 3000;                // between connection attempts
		const long     kConnectTimeoutMs = 1000;
		const long     kPollMs = 50;                   // how often the thread wakes up
		const unsigned kLogEveryNthFailure = 20;       // ~once a minute while the companion is off
	}

	void CompanionLink::Start(unsigned short port, std::string helloLine)
	{
		if (m_running.load())
			return;
		m_port = port;
		m_hello = std::move(helloLine);
		m_running = true;
		m_thread = std::thread(&CompanionLink::Run, this);
		LC_MSG("Companion link: started (127.0.0.1:%u)", port);
	}

	void CompanionLink::Stop()
	{
		if (!m_running.exchange(false))
			return;
		if (m_thread.joinable())
			m_thread.join();      // the thread checks m_running at least every kPollMs
		LC_MSG("Companion link: stopped");
	}

	void CompanionLink::Send(const std::string& line)
	{
		if (!m_connected.load())
		{
			LC_MSG("Companion link: not connected, dropped: %s", line.c_str());
			return;
		}
		std::lock_guard<std::mutex> lock(m_outLock);
		if (m_outgoing.size() >= kMaxQueued)
		{
			LC_WARN("Companion link: send queue full, dropped a message");
			return;
		}
		m_outgoing.push_back(line);
	}

	void CompanionLink::Poll(std::vector<LinkEvent>& out, size_t max)
	{
		std::lock_guard<std::mutex> lock(m_inLock);
		while (!m_incoming.empty() && out.size() < max)
		{
			out.push_back(std::move(m_incoming.front()));
			m_incoming.pop_front();
		}
	}

	void CompanionLink::Push(LinkEvent ev)
	{
		std::lock_guard<std::mutex> lock(m_inLock);
		if (m_incoming.size() >= kMaxQueued)
		{
			LC_WARN("Companion link: receive queue full, dropped a message");
			return;
		}
		m_incoming.push_back(std::move(ev));
	}

	// ---- background thread -----------------------------------------------------

	void CompanionLink::Run()
	{
		WSADATA wsa;
		if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
		{
			LC_ERR("Companion link: Winsock failed to start; no companion connection this session");
			m_running = false;
			return;
		}

		ULONGLONG nextAttempt = 0;
		char buf[4096];

		while (m_running.load())
		{
			if (!m_connected.load())
			{
				if (GetTickCount64() >= nextAttempt)
				{
					if (!TryConnect())
						nextAttempt = GetTickCount64() + kRetryMs;
				}
				else
				{
					Sleep(kPollMs);
				}
				continue;
			}

			SOCKET s = static_cast<SOCKET>(m_socket);

			// 1. Send anything queued by the main thread.
			std::deque<std::string> toSend;
			{
				std::lock_guard<std::mutex> lock(m_outLock);
				toSend.swap(m_outgoing);
			}
			bool ok = true;
			for (auto& line : toSend)
			{
				LC_MSG("-> companion: %s", line.c_str());
				if (!SendAll(line + "\n")) { ok = false; break; }
			}
			if (!ok)
			{
				LC_MSG("Companion link: send failed (error %d)", WSAGetLastError());
				CloseSocket(true);
				nextAttempt = GetTickCount64() + kRetryMs;
				continue;
			}

			// 2. Wait briefly for incoming data.
			fd_set readSet;
			FD_ZERO(&readSet);
			FD_SET(s, &readSet);
			timeval tv{ 0, kPollMs * 1000 };
			int ready = select(0, &readSet, nullptr, nullptr, &tv);
			if (ready == SOCKET_ERROR)
			{
				LC_MSG("Companion link: select failed (error %d)", WSAGetLastError());
				CloseSocket(true);
				nextAttempt = GetTickCount64() + kRetryMs;
				continue;
			}
			if (ready == 0)
				continue;

			int n = recv(s, buf, sizeof(buf), 0);
			if (n <= 0)
			{
				if (n == 0) LC_MSG("Companion link: companion closed the connection");
				else LC_MSG("Companion link: connection lost (error %d)", WSAGetLastError());
				CloseSocket(true);
				nextAttempt = GetTickCount64() + kRetryMs;
				continue;
			}

			// 3. Split into lines.
			m_readBuffer.append(buf, n);
			size_t pos;
			while ((pos = m_readBuffer.find('\n')) != std::string::npos)
			{
				std::string line = m_readBuffer.substr(0, pos);
				m_readBuffer.erase(0, pos + 1);
				if (!line.empty() && line.back() == '\r')
					line.pop_back();
				if (!line.empty())
					Push({ LinkEvent::Kind::Line, std::move(line) });
			}
			if (m_readBuffer.size() > kMaxLine)
			{
				LC_WARN("Companion link: dropped a message over %u bytes", static_cast<unsigned>(kMaxLine));
				m_readBuffer.clear();
			}
		}

		// Stopping: send anything still queued (e.g. game_exit) before closing.
		if (m_connected.load())
		{
			std::deque<std::string> rest;
			{
				std::lock_guard<std::mutex> lock(m_outLock);
				rest.swap(m_outgoing);
			}
			for (auto& line : rest)
			{
				LC_MSG("-> companion: %s", line.c_str());
				if (!SendAll(line + "\n"))
					break;
			}
		}
		CloseSocket(false);
		WSACleanup();
	}

	bool CompanionLink::TryConnect()
	{
		SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
		if (s == INVALID_SOCKET)
			return false;

		sockaddr_in addr{};
		addr.sin_family = AF_INET;
		addr.sin_port = htons(m_port);
		addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);     // 127.0.0.1 only, never the network

		// Non-blocking connect with a timeout, so Stop() is never stuck behind it.
		u_long nonBlocking = 1;
		ioctlsocket(s, FIONBIO, &nonBlocking);
		connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));

		fd_set writeSet, errorSet;
		FD_ZERO(&writeSet); FD_SET(s, &writeSet);
		FD_ZERO(&errorSet); FD_SET(s, &errorSet);
		timeval tv{ 0, kConnectTimeoutMs * 1000 };
		int ready = select(0, nullptr, &writeSet, &errorSet, &tv);

		int soError = 0;
		int len = sizeof(soError);
		getsockopt(s, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&soError), &len);

		if (ready <= 0 || FD_ISSET(s, &errorSet) || soError != 0)
		{
			closesocket(s);
			++m_failedAttempts;
			if (m_failedAttempts == 1 || m_failedAttempts % kLogEveryNthFailure == 0)
				LC_MSG("Companion link: companion not reachable on port %u (attempt %u); will keep trying",
					m_port, m_failedAttempts);
			return false;
		}

		// Back to blocking for simple sends, with a timeout so a stuck companion can't hang us.
		u_long blocking = 0;
		ioctlsocket(s, FIONBIO, &blocking);
		DWORD sendTimeout = 2000;
		setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&sendTimeout), sizeof(sendTimeout));
		BOOL noDelay = TRUE;
		setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&noDelay), sizeof(noDelay));

		m_socket = static_cast<uintptr_t>(s);
		m_readBuffer.clear();
		{
			std::lock_guard<std::mutex> lock(m_outLock);
			m_outgoing.clear();                 // nothing stale from a previous connection
		}
		m_failedAttempts = 0;

		LC_MSG("Companion link: connected");
		LC_MSG("-> companion: %s", m_hello.c_str());
		if (!SendAll(m_hello + "\n"))
		{
			LC_MSG("Companion link: couldn't send hello (error %d)", WSAGetLastError());
			closesocket(s);
			m_socket = ~static_cast<uintptr_t>(0);
			return false;
		}
		m_connected = true;
		Push({ LinkEvent::Kind::Connected, {} });
		return true;
	}

	bool CompanionLink::SendAll(const std::string& data)
	{
		SOCKET s = static_cast<SOCKET>(m_socket);
		size_t sent = 0;
		while (sent < data.size())
		{
			int n = send(s, data.data() + sent, static_cast<int>(data.size() - sent), 0);
			if (n == SOCKET_ERROR)
				return false;
			sent += n;
		}
		return true;
	}

	void CompanionLink::CloseSocket(bool notify)
	{
		if (m_socket != ~static_cast<uintptr_t>(0))
		{
			closesocket(static_cast<SOCKET>(m_socket));
			m_socket = ~static_cast<uintptr_t>(0);
		}
		if (m_connected.exchange(false) && notify)
			Push({ LinkEvent::Kind::Disconnected, {} });
	}
}
