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
