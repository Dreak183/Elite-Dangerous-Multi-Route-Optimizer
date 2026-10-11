# API hardening for every external lookup and plotter — design

Status: Accepted (2026-10-11)
Date: 2026-10-11
Rules: ADR-0002 (fixes in upstream files are minimal and offered upstream). Facts:
`../../../../knowledge/bulk-data.md` ("Spansh API") and `../../../../knowledge/edsm-api.md`.
Inventory of the call sites and their defects: `../../knowledge/upstream-baseline.md`
("External APIs"). This spec extends the Spansh fix of commit `23903c2` to every live call site.

## Purpose

The commander plots and looks up systems from deep space, while Spansh is often overloaded and
does not know freshly visited systems. Every route plot and lookup must then do three things:
- survive transient failures;
- tell the user the real reason in plain English;
- use the nearest known system as the start when the start is unknown.

Dead code (`galaxy_handler.py`, `journal_handler.py`) is not touched.

## Requirements

- A1. **One HTTP path.** Every live external request goes through `edmrn/ext/api.py`:
  - **Requests:** one User-Agent `EDMRN/<edmrn.__version__>`, query values only as encoded
    `params` or a `json` body (never interpolated into the URL), and an explicit timeout.
  - **Retry:** up to 2 retries after 2 s and 5 s, on HTTP 502/503/504, a timeout or a connection
    error. Never on 4xx.
- A2. **Readable failures.** A failure becomes a one-line English reason naming the service, for
  example:
  - `Spansh rejected the request: Could not find starting system`
  - `Spansh is overloaded (HTTP 502). Try again in a minute.`
  - `Spansh did not answer within 30 s.`
  - `No connection to EDSM.`
  - `System 'X' is not known to EDSM.`

  Raw HTML and raw exception text never reach the UI. They go to the log at debug level.
- A3. **"Not found" is distinct from "service failure"** in every lookup, so the user can tell a
  typo from an outage.
- A4. **Coordinate lookup.** `lookup_system(name)` tries the Spansh system search (response
  `results[]` with top-level `x,y,z`; exact, case-insensitive name match) and then EDSM
  `api-v1/system` (`[]` means unknown). It returns coordinates and a source, or a not-found result,
  or a failure that carries both services' reasons.
- A5. **Start fallback** for Neutron and Galaxy. When Spansh answers 400
  `Could not find starting system`:
  - **Coordinates:** taken from the journal when the start equals the journal's current system,
    otherwise from `lookup_system` (EDSM knows systems that Spansh lacks).
  - **Re-plot:** the route is plotted once more from Spansh's nearest known system, and the user is
    told what was substituted and at what distance.
  - **Destination:** any other 400, including an unknown destination, is shown verbatim (A2).
- A6. **Result polling** (Neutron and Galaxy):
  - It waits by wall-clock time, 180 s for Neutron and 300 s for Galaxy, polling every 1 to 3 s.
  - Up to 3 consecutive transient failures are tolerated; a 200 resets the count.
  - When the time runs out, the message says the job may still finish on Spansh's website.
- A7. **Galaxy Plotter:**
  - A5 and A6 apply.
  - The final failure reason stays visible in the output; it is not replaced by "No route data
    returned."
  - The Calculate button is disabled while a job runs and re-enabled when it ends, whatever the
    outcome.
  - The Tk variables are read on the main thread before the worker starts.
- A8. **Neutron:**
  - A5 also covers a typed start.
  - A6 replaces the 20-attempt limit.
  - The result callback (`calculation_done`) runs on the Tk thread.
- A9. **Custom Route:**
  - Coordinates come from `lookup_system`.
  - A not-found system gives `System 'X' was not found at Spansh or EDSM. Check the spelling.`
  - A service failure gives `Could not look up 'X': <Spansh reason>; <EDSM reason>`.
  - When optimising, a start or end whose coordinates cannot be obtained stops the optimisation
    with that message, instead of being silently dropped.
- A10. **System Info (EDSM):**
  - The system name is sent as an encoded parameter (A1).
  - An unknown system shows `System 'X' is not known to EDSM.` instead of "Data fetched".
  - A failed bodies, stations, factions or EDAstro request keeps everything already loaded and adds
    a note per missing part.
  - A list-shaped factions body no longer raises.
  - Network failures no longer write `edmrn_crash.log`.
- A11. **Nearest by coordinates:**
  - The distance is read from `system.distance`.
  - The request goes through A1, and failures use A2.
- A12. **Autocomplete:**
  - After a transient failure or timeout, that service is skipped for 60 s (EDSM is still tried).
  - The Custom Route tab uses the app's single autocompleter, so its cache is shared.
  - Suggestions are delivered to widgets on the Tk thread.
  - No retries: latency matters more than completeness here.
- A13. **Update check:**
  - It uses A1 (one attempt, no retry).
  - A failed manual check says `Could not check for updates: <reason>` instead of claiming the
    app is up to date.
  - The upstream repository URL stays.
- A14. **No test uses the network.** Live behaviour is verified by a documented manual check
  (`../../how-to/run.md`).

## Design

### `edmrn/ext/api.py` (new, ours)

- `USER_AGENT = f"EDMRN/{edmrn.__version__}"`.
- **`ApiFailure`** is a frozen dataclass with:
  - `service` (`"Spansh"`, `"EDSM"`, `"EDAstro"` or `"GitHub"`);
  - `kind` (`rejected`, `overloaded`, `timeout`, `offline` or `unexpected`);
  - `status: int | None`;
  - `detail: str`, the service's own reason when it gave one.

  `str(failure)` is the A2 sentence. **`ApiError(Exception)`** carries an `ApiFailure`.
- **`call(method, url, *, service, params=None, json=None, timeout, retries=2, delays=(2.0, 5.0),
  on_retry=None, transport=requests.request, sleep=time.sleep) -> requests.Response`** behaves as
  follows:
  - 2xx and 4xx responses are returned; callers decide what a 404 or 400 means.
  - A transient status that persists after the retries raises `ApiError(overloaded)`.
  - A persistent timeout or connection error raises `ApiError(timeout)` or `ApiError(offline)`.
  - Other 5xx raises `ApiError(unexpected)`.
- **`failure_from_response(response, service) -> ApiFailure`** reads a JSON `error` (Spansh) or
  `msg` (EDSM) when present.
- **`lookup_system(name, *, call=call) -> Lookup`** returns one of:
  - `Lookup(name, coords, source)`;
  - `Lookup.not_found(name)`;
  - `Lookup.failed(name, failures)`.

  The result is "not found" only when both services answered and neither knows the name. When one
  service failed and the other does not know the name, the result is "failed" (A3).
- **`nearest_known_system(x, y, z, *, call=call) -> tuple[str, float] | None`** moves here from
  `spansh.py`.

### `edmrn/ext/spansh.py` (existing; rebuilt on `api.py`)

- `submit_route_job(url, params, *, start_coords=None, coords_lookup=lookup_system, on_retry=None,
  call=call) -> RouteSubmission(response, note, used_from)` is shared by Neutron and Galaxy. On an
  unknown start it uses `start_coords`, or else `coords_lookup(params["from"])`, then
  `nearest_known_system`, then re-submits once (A5).
- `poll_route_result(job_id, *, max_wait, on_progress=None, call=call, clock=time.monotonic,
  sleep=time.sleep) -> dict` implements A6. It returns the 200 body or raises `ApiError`.
- `spansh_error` and `post_with_retry` are removed once their callers have moved. The existing
  tests are rewritten against the new functions.

### Upstream call sites (live only; minimal diffs, offered upstream)

| Site | Change |
|---|---|
| `neutron.py` `calculate_route`, `_wait_for_result` | `submit_route_job` (typed-start fallback), `poll_route_result(max_wait=180)`; error text `str(ApiFailure)` |
| `neutron_manager.py` `calculate_neutron_route` | `calculation_done` dispatched via `root.after(0, …)` |
| `galaxy_plotter.py` `submit_route_job`, `poll_route_results` | delegate to `ext.spansh` (A5/A6); return a failure reason instead of `None` |
| `app.py` `_calculate_galaxy_route`, `_display_galaxy_route_result` | read Tk vars before the thread; disable/enable the button; pass journal coords when the source equals the journal system; keep the failure reason visible |
| `custom_route.py` `_fetch_coordinates`, its callers' messages, `optimize_route` | `lookup_system`; A9 messages; stop instead of dropping start or end |
| `app.py` `fetch_edsm_system_data` | `call(..., params=…)`; A10 |
| `app.py` `_find_nearest_system_by_coordinates`, `_show_nearest_results` | `call`; distance from `system`; A2 |
| `system_autocomplete.py` | `call` (no retries) with a 60 s per-service cooldown; results to widgets via the app's Tk thread |
| `custom_route.py` `_get_suggestions` | use the app's autocompleter |
| `updater.py` `check_for_updates` and the manual check | `call(retries=0)`; honest failure message |

The plan maps each row to exact lines. Each row becomes one hook-point row in
`../../architecture/overview.md`, in the same commit.

## Error handling

All of it is A2/A3: a single sentence per failure in the place the feature already reports errors
(dialog, status label, output box or info label). Nothing raises into Tk. Unexpected exceptions
inside our helpers are logged with traceback at error level and shown as
`Unexpected error talking to <service>`.

## Testing

pytest; the fake `transport` and `sleep` are injected; no network.
- **`api.py`:**
  - retry matrix (502→202, 502×3, timeout×3, connection error then 200, 400 without retry, 500);
  - failure sentences for Spansh JSON, EDSM `msg`, HTML 502, timeout and offline;
  - User-Agent and encoded params present on every call;
  - `lookup_system`: Spansh hit, Spansh miss then EDSM hit, both miss, Spansh 502 with EDSM miss
    (failed, not "not found"), name-match case-insensitivity.
- **`spansh.py`:**
  - unknown start with journal coords, with looked-up coords, with lookup not found, and with the
    nearest being the same system;
  - other 400s;
  - polling: 202→200; transient×2 then 200; transient×3; time-out by a fake clock.
- **Sites, with fake apps and widgets where the logic is reachable without Tk:**
  - Neutron and Galaxy failure texts;
  - Galaxy button state on success and failure;
  - Custom Route messages and the stop-on-missing-start rule;
  - System Info: unknown system, partial failure, list-shaped factions;
  - Nearest distance;
  - autocomplete cooldown and shared cache;
  - updater failure message.
- **Manual live check** (documented in `../../how-to/run.md`, run once at the end):
  - a Neutron and a Galaxy plot from `Blae Phoe XT-A e1459`;
  - a typed unknown start;
  - a Custom Route with a Spansh-unknown system;
  - System Info for `Sol` and for a nonsense name;
  - Nearest at `0,0,0`.

## Not in scope

- Dead code in `galaxy_handler.py` and `journal_handler.py`.
- The copy in `../../../DrAssist/`.
- Tk thread use outside the touched flows.
- Pointing the updater at a fork (no fork yet).
