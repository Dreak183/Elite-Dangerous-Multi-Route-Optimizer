# CLAUDE.md — EDMRN

Rules only; detail lives in `docs/`. Keep this file short.

## Read these first

1. `docs/handover/HANDOVER.md` — where the work stands. **Always start here.**
2. `docs/README.md` — documentation map and the maintenance rules that bind you.
3. `docs/architecture/` — before writing code; `decisions/` before changing a settled point.

## What this project is

EDMRN is a fork and continuation of *ED Multi Route Navigation*
(NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer, AGPL-3.0), a desktop route planning and
optimization tool for Elite Dangerous. The fork adds current-location detection from the journal, a
library of saved routes, system bookmarks, and an exchange channel for a small group of friends — a
wing leader builds a route and sends it to the others; peer-to-peer preferred, an own server if
necessary, chosen by a researched comparison of the options.

The fork stays close to upstream and merges `upstream/main` regularly (remote `upstream`).

This folder is its own git repository (branch `main`) inside the `EDProjects` hub. The hub's
`../CLAUDE.md` loads automatically and holds the rules shared by all projects.

## Stack

Upstream's stack, unchanged: Python ≥ 3.13 (`setup.py`), customtkinter, tksheet, pandas, scipy,
python-tsp, matplotlib, requests (Spansh and EDSM), PyInstaller (`edmrn.spec`). Dependencies are
declared in upstream's `requirements.txt`; ours go in `requirements-edmrn.txt`. Locally, uv runs the
environment without a packaging rewrite: `uv venv` + `uv pip install -r requirements.txt -r
requirements-edmrn.txt`. Tests: pytest under `tests/` (upstream ships none).

## Hard rules

- **Upstream files are touched minimally.** No reformatting, renaming or sweeping refactors of
  upstream code; our features live in new modules under `edmrn/ext/`, upstream files get only small
  hook points. Every upstream diff is merge-conflict surface (ADR-0002).
- **AGPL-3.0 stays.** New dependencies must be AGPL-compatible.
- **Journal data comes from upstream's journal layer**, never from EDJournalData (ADR-0001).
- **Data received from friends is untrusted.** Routes and bookmarks are validated against a schema
  before use, never executed, never placed unchecked into paths, SQL or UI markup.
- **No transport code before the transport ADR.** P2P vs. server is researched and decided first.
- **Tests never touch the real journal folder.** Upstream derives it from `~`; redirect
  `HOME`/`USERPROFILE` and pass explicit paths.
- **Secrets stay in `.env`** (git-ignored, read access denied in `.claude/settings.json`).
- **Documentation is part of the change** — same commit (`docs/README.md`).
- **Hand over early** (`docs/handover/protocol.md`).

## Model policy for delegation

Every subagent gets an explicit `model`.

| Work | Model |
|---|---|
| Orchestration | Opus (the main session) |
| Highly complex planning: new architecture, data-model redesign | Fable |
| Ordinary planning, reviews, debugging analysis | Opus |
| Writing code, tests, refactors | Sonnet |
| Mechanical edits | Haiku |

## Workflow

`superpowers:brainstorming` → spec in `docs/superpowers/specs/` → `writing-plans` → plan in
`docs/superpowers/plans/` → `subagent-driven-development` with TDD → `requesting-code-review` →
`finishing-a-development-branch`. Bugs start with `systematic-debugging`. The spec is authority.

## Research before implementing

`docs/knowledge/` (project notes) → `../knowledge/` (shared facts; index `../knowledge/README.md`;
for this project: `reference-data.md` for location events and `Status.json`, `journal-tools.md`,
`bulk-data.md` for Spansh, `edsm-api.md`, `local-ipc-and-storage.md` for SQLite, `claude-code.md`) →
`../ED_journal_dev/` → upstream → general sources. General findings go to `../knowledge/` under its
rules; project consequences to `docs/knowledge/`.
