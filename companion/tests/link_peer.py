"""Test helper: runs the real GameLink server for the plugin's C++ connection
test (plugin/tests/run_tests.ps1). Not a pytest test.

Replies "pong" to event "ping", closes the connection on "please_disconnect",
records whether "game_exit" arrived, and exits after a time limit.

Usage: python -m tests.link_peer <port> <result_file>
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from living_cyrodiil.game_link import GameLink


async def main(port: int, result_file: Path) -> None:
    link = GameLink(port=port)
    seen: list[str] = []

    async def on_event(msg: dict) -> None:
        name = msg.get("name")
        seen.append(name)
        if name == "ping":
            await link.notify("pong")
        elif name == "please_disconnect":
            if link._writer:
                link._writer.close()
        elif name == "game_exit":
            result_file.write_text("game_exit received\n", encoding="utf-8")

    link.on("event", on_event)
    await link.start()
    print(f"peer listening on {port}", flush=True)
    await asyncio.sleep(25)
    await link.stop()


if __name__ == "__main__":
    # Log to the screen only, so test runs never mix into the real companion.log.
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    asyncio.run(main(int(sys.argv[1]), Path(sys.argv[2])))
