"""Checks that contracts/ and docs/data-and-api.md use the same names.

- Every table in the docs' schema tables exists in contracts/schema.sql, with every listed column.
- Every path in contracts/openapi.yaml appears in the docs' endpoint table.
- Every Realtime table in the docs appears in contracts/events.md.

Run: py -3.12 scripts/check_contracts.py   (exit 1 on any mismatch)
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "data-and-api.md").read_text(encoding="utf-8")
SCHEMA = (ROOT / "contracts" / "schema.sql").read_text(encoding="utf-8")
OPENAPI = (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
EVENTS = (ROOT / "contracts" / "events.md").read_text(encoding="utf-8")

# Columns the docs describe as groups rather than names.
GROUP_WORDS = {"status timestamps", "token counts"}
# Doc names that are JSON keys inside a column, not columns.
JSON_KEYS = {"paused", "tone", "feedback_delay_minutes", "alert_phone", "model", "hosted", "marketing", "owner",
             "meta_callback", "shift_manager", "manager", "turn_done"}

errors: list[str] = []


def table_bodies() -> dict[str, str]:
    bodies = {}
    for m in re.finditer(r"create table (\w+) \((.*?)\n\);", SCHEMA, re.S):
        bodies[m.group(1)] = m.group(2)
    return bodies


def section(title: str) -> str:
    m = re.search(rf"^## {title}\n(.*?)(?=^## )", DOC, re.S | re.M)
    return m.group(1) if m else ""


tables = table_bodies()
schema_part = section("Schema")
for row in re.findall(r"^\| `(\w+)` \| (.*?) \|", schema_part, re.M):
    name, cols = row
    if name not in tables:
        errors.append(f"table {name}: in docs, missing from schema.sql")
        continue
    body = tables[name]
    # Only the "Key columns" cell: drop parenthesised value lists such as `status` (`a` / `b`).
    cols_clean = re.sub(r"\([^)]*\)", "", cols)
    for col in re.findall(r"`(\w+)`", cols_clean):
        if col in JSON_KEYS:
            continue
        if not re.search(rf"^\s+{col}\s", body, re.M):
            errors.append(f"column {name}.{col}: in docs, missing from schema.sql")

for path in re.findall(r"^  (/[\w/{}\-]+):\s*$", OPENAPI, re.M):
    if path.startswith("/webhooks"):
        continue
    if f" {path}`" not in DOC and f" {path} " not in DOC:
        errors.append(f"endpoint {path}: in openapi.yaml, missing from docs endpoint table")

realtime = section("Realtime")
for name in re.findall(r"^\| `(\w+)` \| (?:INSERT|UPDATE)", realtime, re.M):
    if f"`{name}`" not in EVENTS:
        errors.append(f"realtime {name}: in docs, missing from events.md")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"contracts OK: {len(tables)} tables, docs and contracts agree")
