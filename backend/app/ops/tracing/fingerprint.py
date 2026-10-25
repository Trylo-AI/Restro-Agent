"""Failure fingerprint (PDS §7.1): the same failure groups together across runs whatever ids it mentions."""

import hashlib
import re

_NORMALISE = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\\/-]+"), "<path>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\d+"), "<n>"),
]


def normalise(reason: str | None) -> str:
    s = reason or ""
    for rx, repl in _NORMALISE:
        s = rx.sub(repl, s)
    return s.strip()


def fingerprint(stage: str | None, code: str | None, error_type: str | None, reason: str | None) -> str:
    key = "|".join([stage or "", code or "", error_type or "", normalise(reason)])
    return hashlib.sha1(key.encode()).hexdigest()[:16]  # noqa: S324  (grouping key, not security)


_SQL_LITERALS = [
    (re.compile(r"'(?:[^']|'')*'"), "?"),
    (re.compile(r"\$\d+"), "?"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "?"),
    (re.compile(r"\s+"), " "),
]
_TABLE = re.compile(r"\b(?:from|into|update|join)\s+\"?([a-z_][a-z0-9_]*)", re.I)


def statement(sql: str) -> tuple[str, str, str | None]:
    """(operation, statement_hash, first table) for a SQL statement; values never leave this function."""
    norm = sql
    for rx, repl in _SQL_LITERALS:
        norm = rx.sub(repl, norm)
    norm = norm.strip().lower()
    op = norm.split(" ", 1)[0].rstrip(";") if norm else "?"
    m = _TABLE.search(norm)
    return op, hashlib.sha1(norm.encode()).hexdigest()[:12], (m.group(1) if m else None)  # noqa: S324
