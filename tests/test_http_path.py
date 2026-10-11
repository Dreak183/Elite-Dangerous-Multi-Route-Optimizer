"""Binding rule: every live external request goes through edmrn/ext/api.py (spec A1)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "edmrn"
# Dead upstream copies that nothing in the UI reaches (spec "Not in scope").
DEAD = {"journal_handler.py", "galaxy_handler.py"}
DIRECT_HTTP = re.compile(r"\brequests\.(get|post|put|delete|head|patch|request|Session)\s*\(|\burlopen\s*\(")


def test_no_module_but_api_sends_http_itself():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in DEAD or path == ROOT / "ext" / "api.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT_HTTP.search(line):
                offenders.append(f"{path.relative_to(ROOT.parent)}:{number}: {line.strip()}")
    assert offenders == []
