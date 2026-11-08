"""Test isolation: no test may resolve a path to a real user folder (spec R7).

edmrn.logger.get_logger() creates the app data dir and a log file under
Path.home() the first time any upstream module is imported. Test modules import
edmrn at collection time, before fixtures run, so the home variables are
redirected here at module level. Each test then gets its own home on top.
"""
import os
import tempfile

_HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
_SESSION_HOME = tempfile.mkdtemp(prefix="edmrn-test-home-")
for _var in _HOME_VARS:
    os.environ[_var] = _SESSION_HOME

import pytest  # noqa: E402  (must follow the redirect)


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    if request.node.get_closest_marker("relay_e2e"):
        return  # the opt-in end-to-end test talks to wrangler dev on 127.0.0.1 (spec B1 Testing)
    import socket

    def refuse(*args, **kwargs):
        raise RuntimeError("network access in tests is forbidden")

    if request.node.get_closest_marker("loopback"):
        # asyncio on Windows connects a loopback socket pair per event loop; relay tests talk to 127.0.0.1
        real = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex")}
        real_getaddrinfo, real_create = socket.getaddrinfo, socket.create_connection

        def only_loopback(address):
            if not (isinstance(address, tuple) and address and address[0] == "127.0.0.1"):
                refuse()

        def guarded_method(name):
            def method(self, address, *args, **kwargs):
                only_loopback(address)
                return real[name](self, address, *args, **kwargs)
            return method

        def guarded_getaddrinfo(host, *args, **kwargs):
            if host != "127.0.0.1":
                refuse()
            return real_getaddrinfo(host, *args, **kwargs)

        def guarded_create(address, *args, **kwargs):
            only_loopback(address)
            return real_create(address, *args, **kwargs)

        for name in real:
            monkeypatch.setattr(socket.socket, name, guarded_method(name))
        monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
        monkeypatch.setattr(socket, "create_connection", guarded_create)
        return

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in _HOME_VARS:
        monkeypatch.setenv(var, str(home))
    return home


@pytest.fixture(autouse=True)
def no_leftover_sync_threads():
    yield
    import threading
    alive = [t.name for t in threading.enumerate() if t.name == "edmrn-crew-sync" and t.is_alive()]
    assert not alive, "a test left the crew sync thread running; call group.stop()"
