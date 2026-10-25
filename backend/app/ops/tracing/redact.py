"""Redaction for everything the tracer stores (PDS §6.6 rule 10, CLAUDE.md invariant 12).

Keys that may hold text, phones or secrets are dropped (the same list the log scrubber uses). Values are scrubbed
for long digit runs (phones, wa_id, card numbers), emails, bearer tokens, keys in URLs and signed-URL parameters.
"""

import re
from typing import Any

from app.core.logging import FORBIDDEN_KEYS

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"(?i)\b(sig|signature|x-amz-signature|token|key|apikey|api_key|password|secret)=([^&\s\"']+)"),
     r"\1=[redacted]"),
    (re.compile(r"(?i)postgres(ql)?://[^\s\"']+"), "postgres://[redacted]"),
    (re.compile(r"\b(sb_secret_|sb_publishable_|AIza)[A-Za-z0-9_-]{8,}"), "[secret]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\+?\d[\d\s-]{8,}\d"), "[number]"),
]
MAX_TEXT = 500


def text(value: object, limit: int = MAX_TEXT) -> str:
    s = str(value)
    for rx, repl in _PATTERNS:
        s = rx.sub(repl, s)
    return s[:limit]


def value(v: Any, depth: int = 0) -> Any:
    if isinstance(v, str):
        return text(v, 200)
    if isinstance(v, bool | int | float) or v is None:
        return v
    if isinstance(v, dict):
        return mapping(v, depth + 1) if depth < 3 else "[nested]"
    if isinstance(v, list | tuple):
        return [value(x, depth + 1) for x in v[:50]] if depth < 3 else "[nested]"
    return text(v, 200)


def mapping(d: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    return {str(k): ("[removed]" if str(k).lower() in FORBIDDEN_KEYS else value(v, depth)) for k, v in d.items()}


def error(exc: BaseException) -> str:
    """`Type: message`, redacted and capped."""
    msg = str(exc)
    return text(f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__)
