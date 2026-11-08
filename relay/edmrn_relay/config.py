"""Configuration of the crew relay."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
