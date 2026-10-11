# Architecture decision records

One durable decision per file, numbered, never edited after acceptance — superseded by a new ADR
that links back. Template: `_template.md`.

| ADR | Decision |
|---|---|
| [0001](0001-journal-data-from-upstream-layer.md) | Journal data comes from upstream's journal layer, not EDJournalData |
| [0002](0002-stay-close-to-upstream.md) | Stay mergeable with upstream; own code lives in `edmrn/ext/` |
