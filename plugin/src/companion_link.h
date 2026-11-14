#pragma once

// Background connection to the companion program (docs/protocol.md).
//
// All socket work happens on one background thread, so the game never waits.
// The main thread talks to it only through two locked queues:
//   Send(line)      queue a message for the companion
//   Poll(events)    collect what arrived (lines, connects, disconnects)
// If the companion isn't running, the thread quietly retries every few seconds.

#include <atomic>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace lc
{
	struct LinkEvent
	{
		enum class Kind { Connected, Disconnected, Line };
		Kind kind;
		std::string line;     // for Kind::Line: one JSON message, without the newline
	};

	class CompanionLink
	{
	public:
		// helloLine is sent first on every new connection.
		void Start(unsigned short port, std::string helloLine);
		void Stop();                              // waits briefly for the thread to finish

		void Send(const std::string& line);       // dropped if not connected
		void Poll(std::vector<LinkEvent>& out, size_t max = 32);
		bool IsConnected() const { return m_connected.load(); }

	private:
		void Run();
		bool TryConnect();
		void CloseSocket(bool notify);
		void Push(LinkEvent ev);
		bool SendAll(const std::string& data);

		unsigned short     m_port = 0;
		std::string        m_hello;
		std::thread        m_thread;
		std::atomic<bool>  m_running{ false };
		std::atomic<bool>  m_connected{ false };
		uintptr_t          m_socket = ~static_cast<uintptr_t>(0);   // SOCKET, kept opaque here

		std::mutex              m_outLock;
		std::deque<std::string> m_outgoing;
		std::mutex              m_inLock;
		std::deque<LinkEvent>   m_incoming;

		std::string m_readBuffer;
		unsigned    m_failedAttempts = 0;
	};
}
