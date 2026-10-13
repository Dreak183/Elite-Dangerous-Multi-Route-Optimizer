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
- A URL that is itself a secret (a Discord webhook) is passed with `log_url=<redacted form>`. Log
  lines then show `log_url` and only the exception class name, never `repr(exc)`: `requests` errors
  embed the full URL, and the raised `ApiError` does not chain the original exception. The token
  stays out of the log only while the `urllib3` logger is above DEBUG (it logs the full request
  path there); the shipped logging config keeps the root at INFO.
  Nothing else logs such a URL or an exception raised by that call.
- Code that shows a result in Tk receives it through `root.after`.
- Tests inject `call`/`transport`, `sleep` and `clock`; patch the name the calling module
  imported, or pass `call=`; `call=`/`transport=` defaults are bound at import. An autouse fixture
  in `tests/conftest.py` makes `socket.connect`, `getaddrinfo` and `create_connection` raise, so a
  test that reaches the network fails.
- `tests/test_http_path.py` fails when any module other than `api.py` uses the direct forms
  `requests.<verb>(`, `requests.Session(` or `urlopen(`. Aliased imports (`from requests import
  get`, `import requests as ...`) and `http.client` are not detected; review catches them. The
  dead copies `galaxy_handler.py` and `journal_handler.py` are exempt.

Defect it prevents (`../knowledge/upstream-baseline.md`, "External APIs"):
- Each call site had its own User-Agent, timeout and error handling, and only Neutron retried.
- EDSM URLs interpolated raw system names, so `&`, `#` and `+` broke System Info.
- One 502 aborted Galaxy jobs that were still running.
- Raw HTML and exception text reached the UI.
- A Spansh outage looked like "System Not Found".
- A webhook token reached the log through `requests` exception text (`log_url`).
