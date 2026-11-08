"""Replay the newest journal files into a fresh tracker (the cold start of a journal-fed tracker).

Shared by the location and ship trackers: upstream's monitor reads the newest journal from its end
and never sees the session's earlier events.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Callable, Optional

from edmrn.logger import get_logger

logger = get_logger('JournalReplay')


def _mtime_or_zero(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def replay_newest(journal_dir: Optional[str], make_tracker: Callable[[], Any],
                  accept: Callable[[Any], bool], max_files: int = 5) -> Optional[Any]:
    """Feed the newest ``max_files`` journals, newest first, each into a fresh tracker.

    Returns the first replay tracker whose ``state`` ``accept`` approves, else None. The tracker
    needs ``feed(event)`` and ``state``.
    """
    if not journal_dir or not os.path.isdir(journal_dir):
        return None
    try:
        files = sorted(glob.glob(os.path.join(glob.escape(journal_dir), 'Journal.*.log')),
                       key=_mtime_or_zero, reverse=True)
    except OSError as e:
        logger.error(f"Cold start could not list journals: {e}")
        return None
    for path in files[:max_files]:
        replay = make_tracker()
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if '"event"' not in line:
                        continue
                    try:
                        replay.feed(json.loads(line))
                    except (ValueError, RecursionError):
                        continue
        except OSError as e:
            logger.warning(f"Cold start skipped {os.path.basename(path)}: {e}")
            continue
        if accept(replay.state):
            return replay
    return None
