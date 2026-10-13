# Documentation map

Everything a new agent, developer or project manager needs, and nothing that duplicates the code.
`../CLAUDE.md` is the entry point and stays short; this tree holds the detail.

| Folder | Holds | Read it when |
|---|---|---|
| `handover/` | `HANDOVER.md` — the single, always-current pickup point; `protocol.md` — when and how to write one | Starting a new session. **Read this first.** |
| `architecture/` | `overview.md` (layers, invariants), `patterns.md` (binding code patterns) | Before writing or reviewing any code |
| `architecture/decisions/` | Numbered ADRs — one durable decision each, with its consequence | Before changing something an ADR already settled |
| `how-to/` | Running, testing, releasing; `deploy-relay.md` — deploying and operating the crew relay | Running, testing, committing, releasing, deploying the relay |
| `knowledge/` | Project-specific notes on external facts; shared facts live in `../../knowledge/` | Before touching external data |
| `cowork/` | `BACKLOG.md` — this project's research brief for Claude Cowork (rules: `../../cowork/INSTRUCTIONS.md`) | Handing research to Cowork |
| `superpowers/specs/` | Approved design specs — the binding authority | Resolving what the system is supposed to do |
| `superpowers/plans/` | Implementation plans — an argument from the spec, not authority | Executing tasks |

## Precedence when documents disagree

`spec` > `ADR` > `plan` > `code comment`. A plan that contradicts the spec is a plan defect: rule on
it, record the ruling, fix the plan text. A code comment that contradicts an ADR is stale — delete it.

## Maintenance rules (binding)

These exist because stale documentation is worse than none: it gets followed.

1. **Same commit.** A change that invalidates a document updates that document in the *same* commit.
2. **Delete, don't append.** Superseded text is removed, not annotated with "(old)". History lives in
   git. An accepted ADR is never edited; it is superseded by a new ADR that links back.
3. **One home per fact.** A fact lives in exactly one file. Elsewhere, link to it.
4. **No status in prose.** "Currently", "not yet", "will be" belong only in `handover/HANDOVER.md`.
5. **Facts carry a source.** Anything in `knowledge/` states where it was verified and when.
   Anything in `architecture/` states which spec section or ADR it derives from.
6. **Nothing the code already says.** No function inventories, no restated signatures, no changelog
   of past fixes. Document the *why* and the *rule*.
7. **Milestone sweep.** At every phase boundary, before the handover is written: re-read
   `../CLAUDE.md`, `architecture/`, `how-to/` and `knowledge/`, and correct every statement the phase
   falsified.
