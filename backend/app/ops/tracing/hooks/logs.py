"""Log hook (PDS §6.5): every log record carries `run_id` and `span`; WARNING and above during a run become that
run's events. Locally the rendered JSON lines are also written to backend/logs/<service>.jsonl for the Logs page.

Runs after the scrubber in core/logging.py, so events hold ids only, never text, phones or tokens.
"""

import logging
import logging.handlers
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from app.ops.tracing import trace

EVENT_LEVELS = {"warning": "WARNING", "error": "ERROR", "critical": "ERROR", "exception": "ERROR"}
_file: logging.Logger | None = None


def processor(_: Any, method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    r = trace.current_run()
    if r is None:
        return event_dict
    event_dict["run_id"] = r.run_id
    s = trace._span.get()
    if s is not None:
        event_dict["span"] = s.name
    level = EVENT_LEVELS.get(method)
    if level and r.recorded:
        detail = {k: v for k, v in event_dict.items()
                  if k not in ("event", "run_id", "span", "level", "timestamp", "service", "logger")}
        r.add_event(level, str(event_dict.get("event", "")), logger=event_dict.get("logger"), detail=detail or None)
    return event_dict


def open_file(directory: str | Path, service: str) -> None:
    """20 MB × 5 rotations, success lines included."""
    global _file
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path / f"{service}.jsonl", maxBytes=20_000_000, backupCount=5,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"ops.jsonl.{service}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    _file = logger


def tee(_: Any, __: str, line: Any) -> Any:
    """Last processor: the rendered JSON line also goes to the local file."""
    if _file is not None and isinstance(line, str):
        _file.info(line)
    return line
