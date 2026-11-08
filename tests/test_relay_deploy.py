"""Deployment artifacts of the relay: image layout, pins and agreeing ports (Task 8)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from edmrn_relay.config import RelayConfig

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "relay"

EXPECTED_EDMRN = {
    "edmrn/__init__.py", "edmrn/ext/__init__.py", "edmrn/ext/crew_events.py",
    "edmrn/ext/crew_identity.py", "edmrn/ext/crew_roster.py", "edmrn/ext/crew_protocol.py",
}

SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
import edmrn_relay.__main__
print(sorted(m for m in sys.modules if m == "edmrn" or m.startswith("edmrn.")))
"""


def _lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _copy_sources() -> list[str]:
    """Sources of every COPY line of the Dockerfile (the destination is the last token)."""
    sources: list[str] = []
    for line in (RELAY / "Dockerfile").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts and parts[0] == "COPY":
            sources.extend(parts[1:-1])
    return sources


def test_relay_requirements_pin_the_clients_packages_exactly() -> None:
    relay = _lines(RELAY / "requirements.txt")
    assert len(relay) == 2
    assert all(ln.count("==") == 1 for ln in relay)
    client = [ln for ln in _lines(ROOT / "requirements-edmrn.txt")
              if ln.startswith(("cryptography", "websockets"))]
    assert len(client) == 2
    assert sorted(ln.split("==")[0] for ln in relay) == sorted(ln.split(">=")[0] for ln in client)


def test_image_layout_imports_only_the_shared_modules(tmp_path: Path) -> None:
    sources = _copy_sources()
    assert {s for s in sources if s.startswith("edmrn/")} == EXPECTED_EDMRN
    for source in EXPECTED_EDMRN:
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / source, target)
    shutil.copytree(RELAY / "edmrn_relay", tmp_path / "edmrn_relay",
                    ignore=shutil.ignore_patterns("__pycache__"))
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(tmp_path)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("['edmrn', 'edmrn.ext', 'edmrn.ext.crew_events', "
                                     "'edmrn.ext.crew_identity', 'edmrn.ext.crew_protocol', "
                                     "'edmrn.ext.crew_roster']")


def test_dockerignore_admits_every_copy_source() -> None:
    ignore = (RELAY / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore[0] == "*"
    for source in _copy_sources():
        assert f"!{source}" in ignore, source


def test_ports_stop_times_and_entry_points_agree() -> None:
    assert RelayConfig().port == 8765
    dockerfile = (RELAY / "Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8765" in dockerfile
    assert '"-m", "edmrn_relay"' in dockerfile
    assert "reverse_proxy relay:8765" in (RELAY / "Caddyfile").read_text(encoding="utf-8")
    compose = (RELAY / "compose.yaml").read_text(encoding="utf-8")
    assert "dockerfile: relay/Dockerfile" in compose
    assert "stop_grace_period: 30s" in compose
    service = (RELAY / "edmrn-relay.service").read_text(encoding="utf-8")
    assert "-m edmrn_relay" in service
    assert "TimeoutStopSec=30" in service
