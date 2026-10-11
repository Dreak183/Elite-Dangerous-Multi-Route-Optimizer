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
| `edmrn/galaxy_plotter.py` | `submit_route_job` and `poll_route_results` delegate to `edmrn.ext.spansh` and raise `ApiError`; `plot_route` takes `start_coords` and returns `{"error": <reason>}` instead of `None`, and the substitution `note` on success | One 502 or timeout aborted a job still running on Spansh; an unknown start had no fallback; the reason was replaced by "No route data returned." (API hardening spec A5–A7) | yes |
| `edmrn/app.py` (Galaxy) | Import of `journal_start_coords`; `_calculate_galaxy_route` disables the Calculate button, reads the Tk option variables before the worker starts and passes the journal coordinates; `_display_galaxy_route_result` re-enables the button first and shows the failure reason and the substitution note | The worker read Tk variables; the button stayed clickable during a job; the reason was cleared (A7) | yes |
| `edmrn/custom_route.py` (coordinates) | Imports; `_fetch_coordinates` uses the journal coordinates for the current system, else `edmrn.ext.api.lookup_system`, and records the A9 sentence in `lookup_errors`; `_add_system_async` shows it; `optimize_route` stops with `optimize_error` when a start or end has no coordinates, and `_on_optimize_complete` shows it | Every lookup fell through to EDSM because the Spansh reply was misread; every failure said "System Not Found"; an unknown start or end was silently dropped (API hardening spec A9, ruling 4 of the plan) | yes |
