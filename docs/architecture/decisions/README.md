# Architecture decision records

One durable decision per file, numbered, never edited after acceptance — superseded by a new ADR
that links back. Template: `_template.md`.

| ADR | Decision |
|---|---|
| [0001](0001-journal-data-from-upstream-layer.md) | Journal data comes from upstream's journal layer, not EDJournalData |
| [0002](0002-stay-close-to-upstream.md) | Stay mergeable with upstream; own code lives in `edmrn/ext/` |
| [0003](0003-sqlite-for-local-storage.md) | *Superseded by 0004* — SQLite store |
| [0004](0004-sqlite-store-unusable-db-disables-feature.md) | Local fork data lives in one SQLite database, one connection, rollback journal; an unusable database disables the feature |
| [0005](0005-crew-exchange-transport.md) | Crew data travels as a signed event log through a Cloudflare relay; peer-to-peer is a staged fallback |
