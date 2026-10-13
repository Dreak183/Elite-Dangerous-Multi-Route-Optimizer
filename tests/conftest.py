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
def no_network(monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise RuntimeError("network access in tests is forbidden")

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
