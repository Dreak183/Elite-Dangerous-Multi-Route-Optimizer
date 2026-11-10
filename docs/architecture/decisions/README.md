# Architecture decision records

One durable decision per file, numbered, never edited after acceptance — superseded by a new ADR
that links back. Template: `_template.md`.

| ADR | Decision |
|---|---|
| [0001](0001-journal-data-from-upstream-layer.md) | Journal data comes from upstream's journal layer, not EDJournalData |
| [0002](0002-stay-close-to-upstream.md) | Stay mergeable with upstream; own code lives in `edmrn/ext/` (remotes, merge rhythm and PRs superseded by 0010) |
| [0003](0003-sqlite-for-local-storage.md) | *Superseded by 0004* — SQLite store |
| [0004](0004-sqlite-store-unusable-db-disables-feature.md) | Local fork data lives in one SQLite database, one connection, rollback journal; an unusable database disables the feature (module boundary: see 0006) |
| [0005](0005-crew-exchange-transport.md) | Crew data travels as a signed event log (merge validation: see 0007; transport superseded by 0009) |
| [0006](0006-store-layer-and-crew-modules.md) | The store layer is `store.py` (connection, lock, migrations) plus feature modules that write only through `Store.transaction()` (received-record validation: see 0007) |
| [0007](0007-received-envelopes-stored-projection-gated.md) | A received envelope is stored once signed and well-formed; payload validation and author binding gate only its projection |
| [0008](0008-membership-from-leader-signed-roster.md) | Group membership is computed from leader-signed roster envelopes and gates projection |
| [0009](0009-self-hosted-python-relay.md) | The crew relay is a Python service on the commander's own server, sharing the client's protocol modules; no peer-to-peer transport |
| [0010](0010-independent-project-own-repository.md) | EDMRN develops independently: remote `upstream` is its own repository, the original is merged case by case, no PRs to it |
