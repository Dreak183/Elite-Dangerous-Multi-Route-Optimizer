# ADR-0002: Stay mergeable with upstream; own code lives in `edmrn/ext/`

Status: Accepted
Date: 2026-10-11

## Context

EDMRN is a fork of NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer, which is actively developed.
The commander wants upstream changes merged regularly. Every line changed in an upstream file is a
potential conflict on each merge; a fork that drifts structurally stops being able to take upstream
fixes at all.

## Decision

The repository is a clone of upstream with full history; remote `upstream` points at the original,
`main` carries our work and merges `upstream/main` (procedure: `../../how-to/upstream-sync.md`).
Our features live in new modules under `edmrn/ext/`; upstream files receive only small hook points
(an import, a menu entry, a call). Upstream's `requirements.txt`, `setup.py` and `edmrn.spec` stay as
upstream has them; our dependencies go in `requirements-edmrn.txt`. Our `.gitignore` entries form
one block appended at the end of the file. Changes that are useful upstream are offered as PRs.

## Consequences

Upstream merges stay mostly conflict-free and limited to the hook points. The cost is indirection:
features are wired in through hooks instead of rewriting upstream code in place. A refactor of an
upstream module — however tempting — needs a superseding ADR, because it forfeits clean merges of
that module. The PyInstaller build must be checked after every merge that changes `edmrn.spec`, since
our modules have to be included in the bundle.
