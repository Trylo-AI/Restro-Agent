"""Reader for the local JSON-lines logs (backend/logs/<service>.jsonl and its rotations), for the Logs page.

Newest first, filtered by field match (level, logger, run_id, service, event text), across rotation boundaries.
The cursor is the timestamp of the last line returned: `before` pages back, `after` follows new lines. In the
cloud the logs live in Cloud Logging and the page links there by run_id instead.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
MAX_SCAN_BYTES = 30_000_000


def _files(directory: Path) -> list[Path]:
    """Newest first: api.jsonl, api.jsonl.1, …; all services interleaved by the caller's sort."""
    files = sorted(directory.glob("*.jsonl")) + sorted(directory.glob("*.jsonl.[0-9]*"),
                                                       key=lambda p: int(p.suffix.lstrip(".") or 0))
    return [f for f in files if f.is_file()]


def _lines_backwards(path: Path, budget: int) -> Iterator[str]:
    """The file's lines, last first, reading at most `budget` bytes from the end."""
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        start = max(0, pos - budget)
        tail = b""
        while pos > start:
            step = min(65536, pos - start)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + tail).split(b"\n")
            tail = lines[0]  # may be the end of a line that started in the previous (earlier) block
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line.decode("utf-8", "replace")
        if tail.strip() and start == 0:
            yield tail.decode("utf-8", "replace")


def read(*, level: str | None = None, logger: str | None = None, run_id: str | None = None, service: str | None = None,
         q: str | None = None, before: str | None = None, after: str | None = None, limit: int = 200,
         directory: Path = LOG_DIR) -> dict[str, Any]:
    if not directory.exists():
        return {"available": False, "lines": [], "cursor": None}
    levels = set(level.split(",")) if level else None
    out: list[dict[str, Any]] = []
    budget = MAX_SCAN_BYTES
    for path in _files(directory):
        if service and not path.name.startswith(f"{service}.jsonl"):
            continue
        size = path.stat().st_size
        for raw in _lines_backwards(path, min(size, budget)):
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            ts = rec.get("timestamp", "")
            if before and ts >= before:
                continue
            if after and ts <= after:
                break
            if levels and rec.get("level") not in levels:
                continue
            if logger and rec.get("logger") != logger:
                continue
            if run_id and rec.get("run_id") != run_id:
                continue
            if q and q.lower() not in raw.lower():
                continue
            rec["_file"] = path.name
            out.append(rec)
            if len(out) >= limit * 3:
                break
        budget -= size
        if budget <= 0:
            break
    out.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    out = out[:limit]
    return {"available": True, "lines": out, "cursor": out[-1]["timestamp"] if out else before,
            "newest": out[0]["timestamp"] if out else after}
