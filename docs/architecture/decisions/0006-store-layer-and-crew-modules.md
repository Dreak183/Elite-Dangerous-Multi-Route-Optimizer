# ADR-0006: The store layer is `store.py` plus feature modules that go through its one connection

Status: Accepted
Date: 2026-10-12
Supersedes: [ADR-0004](0004-sqlite-store-unusable-db-disables-feature.md) on one point only. ADR-0004
said the database "is accessed only through `edmrn/ext/store.py`" and that friends' records enter
"only through validating functions in `store.py`". The crew features (crew capture spec, Spec A)
need their own tables, insert path and queries; putting them into `store.py` would make one file
own every feature's SQL. Everything else in ADR-0004 stays in force unchanged.

## Context

ADR-0004's intent is one connection, one lock, ordered migrations, and no SQL built from friends'
content. The crew log adds a dozen tables and a projector. Keeping that logic in `store.py` gains
nothing for the intent and makes the file the merge point of every feature.

## Decision

- `edmrn/ext/store.py` owns the single connection, the lock, the migration list (all features'
  migrations, one `user_version`) and the integrity probe. It exposes `Store.transaction()`.
- A feature module (first: `edmrn/ext/crew_store.py`) holds that feature's SQL. It receives a
  `Store` and writes only inside `Store.transaction()`; it never opens a connection.
- Friends' records enter only through the feature module's validating insert function
  (`crew_store.insert_received`), which validates every field before any SQL and uses parameters
  only.
- ADR-0004's other decisions (one file, one connection, rollback journal, `user_version`, an
  unusable database disables the feature, upstream files untouched) apply unchanged.

## Consequences

- Every feature module must go through `Store.transaction()`; a module that opens its own
  connection breaks the one-connection guarantee and the WAL-reset safety ADR-0004 relies on.
- Migrations stay in one ordered list in `store.py`, so a feature's schema change is still visible
  in one place.
