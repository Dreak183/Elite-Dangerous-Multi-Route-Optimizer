"""Fake journal files and polling helpers for tests."""
import json
import os
import time
from pathlib import Path


def event(name, ts="2026-10-11T12:00:00Z", **fields):
    return {"timestamp": ts, "event": name, **fields}


def journal_file(directory: Path, stamp: str = "2026-10-11T120000") -> Path:
    return Path(directory) / f"Journal.{stamp}.01.log"


def append_events(path: Path, *events) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def attached(monitor, path: Path) -> bool:
    """True once the monitor tails `path` and has positioned itself at its end."""
    current = monitor.current_journal_file
    return (current is not None
            and os.path.basename(current) == Path(path).name
            and monitor.last_tell == Path(path).stat().st_size)
