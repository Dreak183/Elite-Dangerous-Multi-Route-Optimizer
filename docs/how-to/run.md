# Run the app and probe upstream code

## Environment

```
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt -r requirements-edmrn.txt
```

`requirements-edmrn.txt` carries pytest, `cryptography` (crew signatures) and `websockets` (crew relay
connection, `edmrn/ext/crew_sync.py`). To add one to an existing environment:
`uv pip install --python .venv/Scripts/python.exe "websockets>=15"`.

Pin 3.13 rather than the newest installed interpreter. It matches `setup.py`'s minimum, while
upstream's README says 3.12+. Later versions have not been tried with this project.

## Start the app against your real data

```
.venv/Scripts/python.exe main.py
```

This reads the real journal folder and writes to `~/Documents/EDMRN_Route_Data`
(`../knowledge/upstream-baseline.md`).

## Start it isolated (smoke tests, agents)

Every path upstream uses derives from `Path.home()`. Redirect all home variables to an empty
directory, so that neither the journal nor the app data dir resolves to real folders (Git Bash):

```
H=<empty temp dir>
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 50 .venv/Scripts/python.exe -u main.py
```

A first start in a fresh `$H` builds matplotlib's font cache first and can take over 25 s to open
the window. Exit code 124 is the timeout ending a healthy run only once the
window was built. Expect "Default journal path not found" warnings. The data dir appears as `$H/EDMRN_Route_Data`, because `$H/Documents` does not exist. The
update check still goes out to GitHub.

To probe a module headless, put a script outside the repository and run it with
`-I -u` and `sys.path.insert(0, <repo>)`. `-u` matters if the script ends with `os._exit`, since the
upstream monitor threads otherwise keep the process alive and buffered output is lost. Write fake
journals into `$H` and pass their folder as `manual_journal_path`.

To check crew capture in an isolated app run, write a journal into the default journal folder under
`$H` before starting (the "Default journal path not found" warning then does not appear):

```
J="$H/Saved Games/Frontier Developments/Elite Dangerous"; mkdir -p "$J"
printf '%s\n' '{"timestamp":"2026-10-12T10:00:00Z","event":"LoadGame","Commander":"Smoke","FID":"F1"}' \
  '{"timestamp":"2026-10-12T10:01:00Z","event":"FSDJump","StarSystem":"Sol","SystemAddress":10477373803,"StarPos":[0,0,0]}' \
  > "$J/Journal.2026-10-12T100000.01.log"
```

After the run, `$H/EDMRN_Route_Data/crew_keys/` holds one key file and the `crew_log` table of
`$H/EDMRN_Route_Data/edmrn.db` holds one `visit`.

## Run the tests

```
.venv/Scripts/python.exe -m pytest
```

From the repository root. `tests/conftest.py` redirects every home variable, so tests are safe
against real data (`../architecture/patterns.md`). No test touches the network. Monitor tests start real threads and poll for up to 5 s each.

## Manual live check of the external APIs

API hardening spec A14. Tests never use the network, so this check is the only proof that Spansh,
EDSM and EDAstro still answer in the shapes `edmrn/ext/` expects. Run it after changing
`edmrn/ext/api.py`, `spansh.py` or `system_info.py`, and after an upstream merge that touches a
call site.

The test system is `Blae Phoe XT-A e1459` (768.375, -565.90625, 14325.34375). Spansh does not know
it and EDSM does (`../../../knowledge/bulk-data.md`, "Spansh API").

Save this script outside the repository, for example as `$H/live_check.py`:

```python
import os, sys
sys.path.insert(0, os.getcwd())
from types import SimpleNamespace
from edmrn.ext.api import lookup_system, nearest_known_system
from edmrn.ext.system_info import fetch_system_data
from edmrn.custom_route import CustomRouteManager
from edmrn.neutron import NeutronRouter
from edmrn.galaxy_plotter import GalaxyPlotter

BLAE = "Blae Phoe XT-A e1459"
for name in (BLAE, "Sol", "Xqzzy Nonsense 404"):
    r = lookup_system(name)
    print("lookup", name, "->", r.coords, r.source, r.message)
print("nearest 0,0,0 ->", nearest_known_system(0, 0, 0))
mgr = CustomRouteManager(SimpleNamespace(journal_monitor=None))
print("custom add", BLAE, "->", mgr.add_system(BLAE), mgr.systems[-1] if mgr.systems else mgr.lookup_errors)
print("custom add nonsense ->", mgr.add_system("Xqzzy Nonsense 404"), mgr.lookup_errors)
sol = fetch_system_data("Sol")
print("system info Sol ->", sol.get("error"), sol.get("name"), len(sol.get("bodies", [])), "bodies", sol.get("notes"))
print("system info nonsense ->", fetch_system_data("Xqzzy Nonsense 404"))
n = NeutronRouter().calculate_route(BLAE, "Sol", 50.0, progress_callback=lambda m: print("  neutron:", m))
print("neutron ->", n.get("success"), n.get("error"), n.get("note"), len(n.get("waypoints", [])), "waypoints")
g = GalaxyPlotter().plot_route(BLAE, "Sol", ship_build="", range_ly=50.0, progress_callback=lambda m: print("  galaxy:", m.replace("\n", " | ")))
print("galaxy ->", g.get("error"), g.get("note"), len(g.get("result", {}).get("system_jumps", [])), "jumps")
os._exit(0)
```

Run it from the repository root with an isolated home (Git Bash):

```
H="$(mktemp -d)"
# save the script above as "$H/live_check.py", then:
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 900 .venv/Scripts/python.exe -I -u "$H/live_check.py"
```

Expected (2026-10-11; waypoint and jump counts vary with Spansh's data):
- `lookup Blae Phoe XT-A e1459 -> (768.375, -565.90625, 14325.34375) EDSM None`.
- `lookup Sol -> (0.0, 0.0, 0.0) Spansh None`.
- The nonsense name gives
  `None None System 'Xqzzy Nonsense 404' was not found at Spansh or EDSM. Check the spelling.`
- `nearest 0,0,0 -> ('Sol', 0.0)`.
- Custom Route adds Blae Phoe with the EDSM coordinates; the nonsense name gives `False` and the
  same sentence.
- System Info for Sol: no error and about 40 bodies. For the nonsense name:
  `{'error': "System 'Xqzzy Nonsense 404' is not known to EDSM."}`.
- Neutron and Galaxy each succeed with the note
  `Start system 'Blae Phoe XT-A e1459' is not known to Spansh yet - plotted from nearest known
  system 'Blae Phoe SS-B d751' (10.6 ly away).`.

A line that reads `... is overloaded (HTTP 502). Try again in a minute.` or `... did not answer
within ...` is Spansh under load, not a defect: rerun later. Any `Traceback`, any HTML in a
message, or a nonsense name reported as anything but "not found" is a defect.

Then start the app isolated (section "Start it isolated" above) and check its output and the log
under `$H/EDMRN_Route_Data/logs/` for `Traceback`.

If someone can look at the window, also check by hand:
- **Neutron and Galaxy:** plot from `Blae Phoe XT-A e1459` to `Sol`; both show the substitution
  note. While Galaxy runs, its button is disabled.
- **Typed unknown start:** Neutron from `Xqzzy Nonsense 404` shows
  `Spansh rejected the request: Could not find starting system`.
- **Custom Route:** add `Blae Phoe XT-A e1459`.
- **System Info:** `Sol` loads; the nonsense name shows `System '…' is not known to EDSM.`
- **Nearest:** `0,0,0` offers `Sol` at `0.00 LY`.
