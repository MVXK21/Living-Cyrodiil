"""Tests for the companion's game connection, using a fake game client.
Runs a real local socket on a random free port; no game needed."""

from __future__ import annotations

import asyncio
import json

from living_cyrodiil.game_link import PROTOCOL_VERSION, GameLink, decode, encode

PLUGIN_HELLO = {"type": "hello", "protocol": PROTOCOL_VERSION, "side": "plugin",
                "plugin_version": "test", "game": "oblivion"}


async def _client(port: int):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    async def send(obj):
        writer.write((obj if isinstance(obj, bytes) else encode(obj)))
        await writer.drain()

    async def recv():
        line = await asyncio.wait_for(reader.readline(), 2)
        return json.loads(line) if line else None

    return reader, writer, send, recv


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


def test_encode_decode_round_trip():
    msg = {"type": "notify", "text": 'Quotes " and ümlauts'}
    assert decode(encode(msg)) == msg
    assert encode(msg).endswith(b"\n")
    assert decode(b"not json\n") is None
    assert decode(b'{"no_type": 1}\n') is None
    assert decode(b"[1, 2]\n") is None


def test_handshake_events_and_notify():
    async def scenario():
        link = GameLink(port=0)
        events, connects = [], []
        link.on("event", lambda m: events.append(m["name"]))
        link.on_connect = lambda hello: connects.append(hello["plugin_version"])
        await link.start()
        _, writer, send, recv = await _client(link.bound_port)

        await send(PLUGIN_HELLO)
        reply = await recv()
        assert reply["type"] == "hello" and reply["side"] == "companion"
        assert reply["protocol"] == PROTOCOL_VERSION
        assert connects == ["test"]

        await send(b"garbage that is not json\n")            # ignored, connection survives
        await send({"type": "something_new", "x": 1})      # unknown type: ignored
        await send({"type": "event", "name": "save_loaded"})
        await asyncio.sleep(0.1)
        assert events == ["save_loaded"]

        assert await link.notify("Hello from the companion")
        assert (await recv()) == {"type": "notify", "text": "Hello from the companion"}

        writer.close()
        await asyncio.sleep(0.1)
        assert not link.connected.is_set()
        await link.stop()

    _run(scenario())


def test_messages_before_hello_are_ignored():
    async def scenario():
        link = GameLink(port=0)
        events = []
        link.on("event", lambda m: events.append(m))
        await link.start()
        _, writer, send, recv = await _client(link.bound_port)
        await send({"type": "event", "name": "too_early"})
        await send(PLUGIN_HELLO)
        assert (await recv())["type"] == "hello"
        await asyncio.sleep(0.05)
        assert events == []
        writer.close()
        await link.stop()

    _run(scenario())


def test_protocol_mismatch_is_reported_and_closed():
    async def scenario():
        link = GameLink(port=0)
        await link.start()
        reader, writer, send, recv = await _client(link.bound_port)
        await send({**PLUGIN_HELLO, "protocol": 999})
        err = await recv()
        assert err["type"] == "error" and err["code"] == "protocol_mismatch"
        assert await asyncio.wait_for(reader.readline(), 2) == b""   # closed by companion
        assert not link.connected.is_set()
        writer.close()
        await link.stop()

    _run(scenario())


def test_new_connection_replaces_old():
    async def scenario():
        link = GameLink(port=0)
        await link.start()
        r1, w1, send1, recv1 = await _client(link.bound_port)
        await send1(PLUGIN_HELLO)
        await recv1()
        r2, w2, send2, recv2 = await _client(link.bound_port)
        await send2(PLUGIN_HELLO)
        await recv2()
        assert await asyncio.wait_for(r1.readline(), 2) == b""        # old one closed
        assert await link.notify("to the new one")
        assert (await recv2())["text"] == "to the new one"
        w1.close(); w2.close()
        await link.stop()

    _run(scenario())
