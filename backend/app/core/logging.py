"""JSON logs, one event per line, ids only.

Never log tokens, secrets, phone numbers, wa_id or message text (CLAUDE.md invariant 12). The scrubber
drops any field whose name says it might hold one, so a careless `log.info(..., text=msg)` can't leak.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

FORBIDDEN_KEYS = frozenset(
    {
        "text", "body", "payload", "message_text", "reply", "prompt", "note",
        "wa_id", "phone", "phone_number", "display_phone_number", "alert_phone",
        "token", "access_token", "secret", "password", "authorization", "api_key", "jwt",
    }
)


def _scrub(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.lower() in FORBIDDEN_KEYS:
            event_dict[key] = "[removed]"
    return event_dict


def configure_logging(level: str = "info", service: str = "api", log_dir: str | None = None) -> None:
    """`log_dir` (local only): the same JSON lines also go to <log_dir>/<service>.jsonl for the ops Logs page."""
    from app.ops.tracing.hooks import logs as ops_logs

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    if log_dir:
        ops_logs.open_file(log_dir, service)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _scrub,
            ops_logs.processor,  # run_id + span on every line; warnings become the run's events
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
            ops_logs.tee,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
