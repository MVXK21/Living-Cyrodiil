"""The companion's side of the game connection (see docs/protocol.md).

A small asyncio TCP server on 127.0.0.1. The game plugin connects, sends
`hello`, and we answer. One game at a time; a new connection replaces the old.
Message handlers are registered by type, so later steps (talk_start, ...) plug
in without touching the connection code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from . import __version__

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MAX_LINE = 64 * 1024
DEFAULT_PORT = 47833

Handler = Callable[[dict], Awaitable[None] | None]


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict | None:
    """Parse one line. Returns None (and logs) for anything that isn't a JSON object with a type."""
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        log.warning("Ignoring unreadable line from game: %s", e)
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        log.warning("Ignoring message without a type: %r", msg)
        return None
    return msg


class GameLink:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._handlers: dict[str, Handler] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.base_events.Server | None = None
        self.connected = asyncio.Event()
        self.on_connect: Callable[[dict], None] | None = None
        self.on_disconnect: Callable[[], None] | None = None

    def on(self, msg_type: str, handler: Handler) -> None:
        self._handlers[msg_type] = handler

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port,
                                                  limit=MAX_LINE + 2)
        log.info("Waiting for the game on %s:%d", self.host, self.port)

    @property
    def bound_port(self) -> int:
        """The port actually listened on (useful when started with port 0 in tests)."""
        return self._server.sockets[0].getsockname()[1] if self._server else self.port

    async def stop(self) -> None:
        if self._writer:
            self._writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def send(self, msg: dict) -> bool:
        if not self._writer or self._writer.is_closing():
            log.debug("Not sending %s: no game connected", msg.get("type"))
            return False
        data = encode(msg)
        log.info("-> game: %s", data.decode("utf-8").rstrip())
        self._writer.write(data)
        await self._writer.drain()
        return True

    async def notify(self, text: str) -> bool:
        return await self.send({"type": "notify", "text": text})

    # -- connection handling ---------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        if self._writer and not self._writer.is_closing():
            log.info("New game connection from %s replaces the old one", peer)
            self._writer.close()
        self._writer = writer
        log.info("Game connected from %s", peer)
        handshake_done = False
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    log.warning("Dropped a message over %d bytes", MAX_LINE)
                    await reader.read(MAX_LINE)   # skip what's buffered
                    continue
                if not line:
                    break
                msg = decode(line)
                if msg is None:
                    continue
                log.info("<- game: %s", line.decode("utf-8", "replace").rstrip())

                if msg["type"] == "hello":
                    handshake_done = await self._on_hello(msg)
                    if not handshake_done:
                        break
                    continue
                if not handshake_done:
                    log.warning("Ignoring %s before hello", msg["type"])
                    continue

                handler = self._handlers.get(msg["type"])
                if handler is None:
                    log.info("No handler for message type %r (ignored)", msg["type"])
                    continue
                try:
                    result = handler(msg)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.exception("Handler for %r failed", msg["type"])
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None
                self.connected.clear()
                log.info("Game disconnected")
                if self.on_disconnect:
                    self.on_disconnect()
            writer.close()

    async def _on_hello(self, msg: dict) -> bool:
        if msg.get("protocol") != PROTOCOL_VERSION:
            text = (f"Living Cyrodiil: plugin and companion versions don't match "
                    f"(protocol {msg.get('protocol')} vs {PROTOCOL_VERSION}). Update both.")
            log.error(text)
            await self.send({"type": "error", "code": "protocol_mismatch", "text": text})
            return False
        await self.send({"type": "hello", "protocol": PROTOCOL_VERSION,
                         "side": "companion", "companion_version": __version__})
        self.connected.set()
        if self.on_connect:
            self.on_connect(msg)
        return True
