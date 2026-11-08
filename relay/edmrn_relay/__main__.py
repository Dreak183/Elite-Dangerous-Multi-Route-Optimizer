"""Command line of the crew relay: `python -m edmrn_relay`."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable

from .config import ConfigError, RelayConfig, load_config
from .server import RelayServer


async def run(config: RelayConfig, stop: asyncio.Event,
              started: Callable[[int], None] | None = None) -> None:
    """Serve until `stop` is set; `started(port)` is called once the socket listens."""
    server = RelayServer(config)
    port = await server.start()
    try:
        if started is not None:
            started(port)
        await stop.wait()
    finally:
        await server.stop()


async def _main(config: RelayConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    except NotImplementedError:  # Windows
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))
    await run(config, stop)


def main(argv: list[str] | None = None) -> int:
    try:
        config = load_config(argv, os.environ)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    asyncio.run(_main(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
