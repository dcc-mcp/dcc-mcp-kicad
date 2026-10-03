"""Validate the installed package's declarative skill with Core's public API."""

from pathlib import Path

from dcc_mcp_core import validate_skill

import dcc_mcp_kicad

path = Path(dcc_mcp_kicad.__file__).parent / "skills/kicad-board"
report = validate_skill(str(path))
for issue in report.issues:
    print(issue.severity, issue.category, issue.message)
if any(issue.severity == "error" for issue in report.issues):
    raise SystemExit(1)
print("Skill validation passed")
