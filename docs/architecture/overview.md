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

## Upstream hook points (ADR-0002)

Every change we make in an upstream file. On a merge conflict, these are the only lines that may be
ours. "Upstream PR" marks changes meant to be offered upstream.

| File | Change | Why | Upstream PR |
|---|---|---|---|
| `edmrn/neutron.py` | Route submission and result polling go through `edmrn.ext.spansh` (`submit_route_job`, `poll_route_result` for 180 s); the error text is the `ApiFailure` sentence; `start_coords` and the result keys `note` and `start_system` | Spansh answers bad input with 400 and a JSON reason, and 502 or timeouts when overloaded; EDMRN showed `API Error: 400` and gave up after 20 polls (API hardening spec A5, A6, A8) | yes |
| `edmrn/neutron_manager.py` | Range check requires more than 10 LY; passes the journal start coordinates; shows the substituted start; `calculation_done` runs via `root.after` | Spansh rejects ranges of 10 LY or less; a start system unknown to Spansh (explored, undiscovered) is plotted from the nearest known one; the result callback configured Tk widgets from the worker thread (A8) | yes |
