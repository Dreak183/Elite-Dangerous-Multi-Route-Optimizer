"""Routes, invite page and configuration of the relay (no sockets)."""
import hashlib

import pytest

from edmrn_relay.__main__ import main
from edmrn_relay.config import ConfigError, RelayConfig, load_config
from edmrn_relay.join_page import JOIN_CSP, JOIN_HEADERS, JOIN_HTML, JOIN_SCRIPT, greeting
from edmrn_relay.server import HttpReply, group_of, route
from relay_fakes import key_for

V7 = "01920000-0000-7000-8000-0000000000a2"
SRC = "https://example.invalid/edmrn"
TEXT = (("Content-Type", "text/plain; charset=utf-8"),)
NOT_FOUND = HttpReply(404, TEXT, b"not found\n")


def test_greeting_names_the_source_url():
    assert route("GET", "/", False, SRC) == HttpReply(
        200, TEXT, b"EDMRN crew relay. Licence: AGPL-3.0. Source: https://example.invalid/edmrn\n")
    assert greeting("").endswith("Source: not yet published\n")


def test_join_page_is_pinned_byte_for_byte():
    assert hashlib.sha256(JOIN_HTML.encode()).hexdigest() == (
        "44fd9af6fd516f2ba9cb41496354dd066d7253ac1fc5e4ef205fb941e6d6732c")
    assert len(JOIN_HTML) == 1146
    assert JOIN_CSP == (
        "default-src 'none'; script-src 'sha256-Ys1QBGBHzkw97W1xVOxlbYsbsaM7a1646hnDPwqtrs0='; "
        "style-src 'sha256-ccuruHwjqmAReQp4XtXI4ITovYe/9LRSYO7FmD+hsvU='; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")
    assert route("GET", "/join", False, SRC) == HttpReply(200, JOIN_HEADERS, JOIN_HTML.encode())
    assert dict(JOIN_HEADERS)["Referrer-Policy"] == "no-referrer"
    assert dict(JOIN_HEADERS)["Cache-Control"] == "no-store"
    assert f"<script>{JOIN_SCRIPT}</script>" in JOIN_HTML
    for needle in ("textContent", "{1,1987}", "Copy failed"):
        assert needle in JOIN_SCRIPT
    assert "innerHTML" not in JOIN_SCRIPT


def test_unknown_paths_bad_ids_methods_and_plain_gets():
    ws = f"/v1/groups/{V7}/ws"
    for path in ("/nope", "/v1/groups/not-a-uuid/ws", f"/v1/groups/{V7.upper()}/ws",
                 "/v1/groups/01920000-0000-4000-8000-0000000000a2/ws"):
        assert route("GET", path, True, SRC) == NOT_FOUND, path
    assert route("POST", "/", False, SRC) == NOT_FOUND
    assert route("POST", ws, True, SRC) == NOT_FOUND
    assert route("GET", ws, False, SRC) == HttpReply(426, TEXT, b"expected a WebSocket upgrade\n")
    assert route("GET", ws, True, SRC) is None


def test_routes_ignore_the_query_string():
    assert route("GET", "/?x=1", False, SRC) == route("GET", "/", False, SRC)
    assert route("GET", "/join?a", False, SRC) == route("GET", "/join", False, SRC)
    assert route("GET", f"/v1/groups/{V7}/ws?x=1", True, SRC) is None
    assert group_of(f"/v1/groups/{V7}/ws?x=1") == V7
    assert group_of(f"/v1/groups/{V7}/ws/") is None


def test_config_defaults_environment_and_flags(tmp_path):
    assert load_config([], {}) == RelayConfig()
    defaults = RelayConfig()
    assert (defaults.host, defaults.port, defaults.leaders, defaults.source_url,
            defaults.member_quota, defaults.group_quota) == (
        "127.0.0.1", 8765, frozenset(), "", 5000, 80000)
    leader = key_for("leader").author
    env = {"EDMRN_RELAY_HOST": "0.0.0.0", "EDMRN_RELAY_PORT": "8000",
           "EDMRN_RELAY_DB": str(tmp_path / "r.db"), "EDMRN_RELAY_LEADERS": leader,
           "EDMRN_RELAY_SOURCE_URL": SRC, "EDMRN_RELAY_MEMBER_QUOTA": "7",
           "EDMRN_RELAY_GROUP_QUOTA": "9"}
    config = load_config([], env)
    assert config == RelayConfig(host="0.0.0.0", port=8000, db=tmp_path / "r.db",
                                 leaders=frozenset({leader}), source_url=SRC, member_quota=7,
                                 group_quota=9)
    assert load_config(["--port", "9000"], env).port == 9000


def test_config_max_connections():
    assert RelayConfig().max_connections == 256
    assert load_config([], {"EDMRN_RELAY_MAX_CONNECTIONS": "12"}).max_connections == 12
    assert load_config(["--max-connections", "5"], {}).max_connections == 5
    with pytest.raises(ConfigError):
        load_config(["--max-connections", "0"], {})


def test_config_leaders_are_split_and_checked():
    a, b, c = (key_for(n).author for n in ("leader", "alice", "bob"))
    assert load_config(["--leaders", f"{a}, {b}\n{c}"], {}).leaders == frozenset({a, b, c})
    with pytest.raises(ConfigError, match="nope"):
        load_config(["--leaders", "nope"], {})


@pytest.mark.parametrize("args", [["--member-quota", "0"], ["--group-quota", "x"],
                                  ["--port", "70000"]])
def test_config_numbers_are_checked(args):
    with pytest.raises(ConfigError):
        load_config(args, {})


def test_main_refuses_bad_configuration(capsys):
    assert main(["--leaders", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_empty_environment_variables_count_as_unset():
    env = {f"EDMRN_RELAY_{n}": "" for n in
           ("HOST", "PORT", "DB", "LEADERS", "SOURCE_URL", "MEMBER_QUOTA", "GROUP_QUOTA")}
    assert load_config([], env) == RelayConfig()
