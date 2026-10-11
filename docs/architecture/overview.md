# Architecture overview

Layers, data flow and invariants of the system, each derived from a spec section or an ADR.

## Fork layout (ADR-0002)

| Path | Owner |
|---|---|
| `edmrn/` (except `ext/`), `main.py`, `run.py`, `setup.py`, `edmrn.spec`, `requirements.txt`, `assets/`, `sounds/` | upstream — hook points only |
| `edmrn/ext/`, `requirements-edmrn.txt`, `tests/` | EDMRN |
| `CLAUDE.md`, `docs/`, `.claude/` | EDMRN project documentation |

## Invariants

- Journal and `Status.json` data enter only through upstream's journal layer (ADR-0001).
