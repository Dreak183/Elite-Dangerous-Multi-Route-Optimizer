"""Configuration of the crew relay."""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from edmrn.ext.crew_identity import _raw32  # the client's own author check (R2)

DEFAULT_PORT = 8765
DEFAULT_MEMBER_QUOTA = 5000
DEFAULT_GROUP_QUOTA = 80000


@dataclass(frozen=True)
class RelayConfig:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    db: Path = Path("relay.db")
    leaders: frozenset[str] = frozenset()
    source_url: str = ""
    member_quota: int = DEFAULT_MEMBER_QUOTA
    group_quota: int = DEFAULT_GROUP_QUOTA


class ConfigError(ValueError):
    """The command line or the environment holds an unusable value."""


def _leaders(text: str) -> frozenset[str]:
    entries = text.replace(",", " ").split()
    for entry in entries:
        try:
            _raw32(entry)
        except ValueError:
            raise ConfigError(f"leader {entry!r} is not an author (43 base64url characters)") from None
    return frozenset(entries)


def _integer(name: str, text: str, low: int, high: int | None = None) -> int:
    try:
        value = int(text)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {text!r}") from None
    if value < low or (high is not None and value > high):
        bound = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise ConfigError(f"{name} must be {bound}, got {value}")
    return value


def load_config(argv: Sequence[str] | None, environ: Mapping[str, str]) -> RelayConfig:
    """Flags override `EDMRN_RELAY_<NAME>` variables, which override the defaults."""
    defaults = RelayConfig()
    parser = argparse.ArgumentParser(prog="edmrn_relay", description="EDMRN crew relay")
    options = (
        ("host", "--host", defaults.host, "address to listen on"),
        ("port", "--port", str(defaults.port), "TCP port, 0 for an ephemeral one"),
        ("db", "--db", str(defaults.db), "SQLite database file"),
        ("leaders", "--leaders", "", "authors allowed to create groups, comma or space separated"),
        ("source_url", "--source-url", defaults.source_url, "where the source is published"),
        ("member_quota", "--member-quota", str(defaults.member_quota), "events per member and day"),
        ("group_quota", "--group-quota", str(defaults.group_quota), "events per group and day"),
    )
    for dest, flag, default, help_text in options:
        env_name = "EDMRN_RELAY_" + dest.upper()
        parser.add_argument(flag, dest=dest, default=environ.get(env_name) or default, type=str,
                            help=f"{help_text} (env {env_name})")
    args = parser.parse_args(argv)
    return RelayConfig(
        host=args.host,
        port=_integer("port", args.port, 0, 65535),
        db=Path(args.db),
        leaders=_leaders(args.leaders),
        source_url=args.source_url,
        member_quota=_integer("member quota", args.member_quota, 1),
        group_quota=_integer("group quota", args.group_quota, 1),
    )
