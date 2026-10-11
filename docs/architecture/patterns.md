# Binding code patterns

Each pattern states the rule and the defect that motivated it.

## Tests redirect the home folder before importing `edmrn`

Rule: `tests/conftest.py` sets `HOME`, `USERPROFILE`, `APPDATA` and `LOCALAPPDATA` at module level,
before any test module imports `edmrn`. An autouse fixture then gives each test its own home. Never
import `edmrn` from a test helper that loads before `conftest.py`.

Defect it prevents: `edmrn.logger.get_logger()` creates `Documents/EDMRN_Route_Data/logs` under
`Path.home()` when most upstream modules are imported. That happens at test collection, before any
fixture runs, so a fixture-only redirect lets the real Documents folder be written
(`../knowledge/upstream-baseline.md`).

## All external HTTP goes through `edmrn/ext/api.py`

Rule (API hardening spec A1–A3):
- `edmrn.ext.api.call` is the only code that sends a request. It sets the one User-Agent and an
  explicit timeout, and retries 502/503/504, timeouts and connection errors.
- Query values travel only as `params` or a `json` body, never formatted into a URL.
- A path segment taken from service data is validated (an `int` id64) or quoted (a job id).
- Failures become one `ApiFailure` sentence. "Not found" stays distinct from "service failed":
  `lookup_system` returns not found only when every service answered.
- Code that shows a result in Tk receives it through `root.after`.
- Tests inject `call`/`transport`, `sleep` and `clock`; no test uses the network.
- `tests/test_http_path.py` fails when any module other than `api.py` calls `requests` or
  `urlopen` directly. The dead copies `galaxy_handler.py` and `journal_handler.py` are exempt.

Defect it prevents (`../knowledge/upstream-baseline.md`, "External APIs"):
- Each call site had its own User-Agent, timeout and error handling, and only Neutron retried.
- EDSM URLs interpolated raw system names, so `&`, `#` and `+` broke System Info.
- One 502 aborted Galaxy jobs that were still running.
- Raw HTML and exception text reached the UI.
- A Spansh outage looked like "System Not Found".
