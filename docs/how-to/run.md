# Run the app and probe upstream code

## Environment

```
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt -r requirements-edmrn.txt
```

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
HOME="$H" USERPROFILE="$H" APPDATA="$H" LOCALAPPDATA="$H" timeout 25 .venv/Scripts/python.exe -u main.py
```

Exit code 124 is the timeout ending a healthy run. Expect "Default journal path not found"
warnings. The data dir appears as `$H/EDMRN_Route_Data`, because `$H/Documents` does not exist. The
update check still goes out to GitHub.

To probe a module headless, put a script outside the repository and run it with
`-I -u` and `sys.path.insert(0, <repo>)`. `-u` matters if the script ends with `os._exit`, since the
upstream monitor threads otherwise keep the process alive and buffered output is lost. Write fake
journals into `$H` and pass their folder as `manual_journal_path`.

## Run the tests

```
.venv/Scripts/python.exe -m pytest
```

From the repository root. `tests/conftest.py` redirects every home variable, so tests are safe
against real data (`../architecture/patterns.md`). No test touches the network.
