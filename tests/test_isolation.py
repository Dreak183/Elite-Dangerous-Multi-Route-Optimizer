import logging
import os
import socket
from pathlib import Path

import pytest

from edmrn.logger import get_logger

get_logger("Isolation")  # forces upstream's import-time log setup in this session


def test_home_is_redirected_per_test(isolated_home):
    assert Path.home() == isolated_home
    assert Path(os.path.expanduser("~")) == isolated_home


def test_import_time_log_file_lives_in_session_home():
    files = [h.baseFilename for h in logging.getLogger().handlers
             if isinstance(h, logging.FileHandler)
             and h.baseFilename.endswith(".log")]  # skips pytest's own devnull handler
    assert files, "upstream logging did not create a file handler"
    assert all("edmrn-test-home-" in f for f in files), files


@pytest.mark.loopback
def test_loopback_marker_allows_127_0_0_1_only():
    with pytest.raises(RuntimeError):
        socket.create_connection(("192.0.2.1", 9), timeout=0.1)
    with pytest.raises(RuntimeError):
        socket.getaddrinfo("localhost", 80)
    with pytest.raises(RuntimeError):
        socket.socket().connect(("10.255.255.1", 9))
    with pytest.raises(RuntimeError):
        socket.socket().connect_ex(("10.255.255.1", 9))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with socket.create_connection(("127.0.0.1", listener.getsockname()[1])):
            pass


def test_unmarked_tests_cannot_reach_loopback():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(RuntimeError):
            socket.create_connection(("127.0.0.1", listener.getsockname()[1]))


@pytest.mark.loopback
def test_asyncio_connections_are_loopback_only():
    import asyncio

    async def scenario():
        async def serve(reader, writer):
            writer.close()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            for host in ("127.0.0.2", "::1"):
                with pytest.raises(RuntimeError):
                    await asyncio.open_connection(host, port)
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())
