# ADR-0004: Local fork data lives in one SQLite database; an unusable database disables the feature

Status: Accepted; superseded in part (module boundary) by [ADR-0006](0006-store-layer-and-crew-modules.md)
Date: 2026-10-12
Supersedes: [ADR-0003](0003-sqlite-for-local-storage.md). ADR-0003 said a newer database is opened
read-only. The neutron resume spec (N6) requires the feature to be disabled with a visible reason
instead, and the implementation does that. Everything else is carried over unchanged.

## Context

The fork stores data that upstream does not: saved neutron routes first, then the route library and
bookmarks. Upstream keeps state in loose JSON and CSV files in its data dir. Some of them are
rewritten non-atomically, and the backup folders have no index (`../../knowledge/upstream-baseline.md`).
Our features need queries ("routes in progress"), relations (route → waypoints), schema evolution,
and later validated import of data received from friends. The commander chose SQLite over one JSON
file per record.

Python 3.13 bundles SQLite 3.50.4. That version is inside the range of the WAL-reset bug, which needs
two connections writing or checkpointing at the same instant
(`../../../../knowledge/local-ipc-and-storage.md` §3.1).

## Decision

- All fork data lives in one file, `edmrn.db`, in upstream's app data dir
  (`AppConfig.get_app_data_path()`). It is accessed only through `edmrn/ext/store.py`.
- The process holds exactly one connection, guarded by a lock. The journal mode is the default
  rollback journal (`DELETE`), not WAL. `foreign_keys` is on.
- The schema version is `PRAGMA user_version`. Migrations form an ordered list in `store.py`; each
  runs once in its own transaction.
- **An unusable database disables the feature.** This covers a database newer than the code, a file
  that is not a database, and corrupt pages behind an intact header (checked with
  `PRAGMA quick_check` at open). In each case `Store` raises `StoreError`, closes its connection so
  the file can be moved or deleted, and never writes to the file. The feature shows the reason;
  the rest of EDMRN works.
- Upstream's own files stay as upstream writes them. We never move upstream data into the database.

## Consequences

- One file to back up or delete, and SQL instead of folder scans. Python's standard `sqlite3` is
  used, so no new dependency.
- A second process (another EDMRN instance) writing the same file is not supported. The rollback
  journal makes it wait or fail with `database is locked`; it does not corrupt anything.
- Switching to WAL requires a SQLite ≥ 3.50.7 or 3.51.3 runtime and a superseding ADR.
- If a user's data dir is inside a cloud-sync folder, the sync client may lock `edmrn.db` briefly. A
  write then fails with `database is locked` and is retried by the next save; it is not lost
  silently.
- Records received from friends (exchange feature) enter only through validating functions in
  `store.py`, never through raw SQL built from their content (CLAUDE.md hard rule).
