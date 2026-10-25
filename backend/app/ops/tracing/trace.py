"""Run collector and span API (PDS §6.1). Knows nothing about the business domain.

The current run and span live in contextvars, so they follow the work into asyncio tasks (a task copies the
context it was created in). Spans are buffered in memory and written once, when the run ends, by the recorder's
background writer: tracing never adds a DB round trip to the work it measures, and never raises into it.

    async with tracing.run("job", "turn", subject=("pending_work", work_id)) as r:
        with tracing.span("turn.load"):
            ...
        r.finish("RETRYING", error=exc)     # optional; otherwise SUCCEEDED, or FAILED on an exception
"""

import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ops.tracing import names, redact
from app.ops.tracing.fingerprint import fingerprint

log = get_logger(__name__)

MAX_SPANS = 5_000
MAX_EVENTS = 200
SUCCESS = ("SUCCEEDED", "SUCCEEDED_DEGRADED")

_run: ContextVar["Run | None"] = ContextVar("ops_run", default=None)
_span: ContextVar["SpanRec | None"] = ContextVar("ops_span", default=None)
_upstream: ContextVar[tuple[str, str | None] | None] = ContextVar("ops_upstream", default=None)
_warned: set[str] = set()
_db_offset_s = 0.0  # DB clock − this process's clock, measured by the recorder's heartbeat


def set_db_offset(seconds: float) -> None:
    global _db_offset_s
    _db_offset_s = seconds


def to_local(db_time: datetime) -> datetime:
    """A timestamp written by the DB (now()) on this process's clock, so durations across the two are honest."""
    return db_time - timedelta(seconds=_db_offset_s)


def enabled() -> bool:
    return get_settings().ops_tracing_enabled


@dataclass
class SpanRec:
    span_id: int
    parent_id: int | None
    name: str
    level: int
    kind: str
    start_ms: float
    duration_ms: float = 0.0
    outcome: str = "ok"
    target: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Run:
    kind: str
    name: str
    service: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_run_id: str | None = None
    upstream_trace: str | None = None
    upstream_service: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    tenant_id: str | None = None
    attempt: int = 1
    queued_at: datetime | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    t0: float = field(default_factory=time.perf_counter)
    duration_ms: float | None = None
    outcome: str | None = None
    http_status: int | None = None
    error_stage: str | None = None
    error_code: str | None = None
    error_type: str | None = None
    failure_reason: str | None = None
    degradations: list[dict[str, Any]] = field(default_factory=list)
    labels: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, float] = field(default_factory=dict)
    stage_ms: dict[str, float] = field(default_factory=dict)
    dep_ms: dict[str, float] = field(default_factory=dict)
    dep_calls: dict[str, int] = field(default_factory=dict)
    input_size: float | None = None
    input_size_unit: str | None = None
    bytes_in: int | None = None
    bytes_out: int | None = None
    spans: list[SpanRec] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    current_stage: str | None = None
    recorded: bool = True  # False: the kill switch is on, nothing is written
    inserted: bool = False  # a RUNNING row was written at start
    closed: bool = False  # finalised: late spans from background work are ignored
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stage_pending: bool = False

    # ── called by the hooks ────────────────────────────────────────────
    def now_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def next_span_id(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def add_span(self, s: SpanRec) -> None:
        with self._lock:
            if self.closed:
                return
            if len(self.spans) >= MAX_SPANS:
                self.counts["spans_overflow"] = self.counts.get("spans_overflow", 0) + 1
            else:
                self.spans.append(s)
            if s.level == 1 and s.parent_id is None:  # top-level stages only: the critical path, never counted twice
                self.stage_ms[s.name] = round(self.stage_ms.get(s.name, 0.0) + s.duration_ms, 3)
            if s.level == 3 and s.kind != "internal":
                self.dep_ms[s.kind] = round(self.dep_ms.get(s.kind, 0.0) + s.duration_ms, 3)
                self.dep_calls[s.kind] = self.dep_calls.get(s.kind, 0) + 1

    def add_event(self, level: str, message: str, *, logger: str | None = None, detail: dict[str, Any] | None = None,
                  span_name: str | None = None) -> None:
        with self._lock:
            if len(self.events) >= MAX_EVENTS:
                self.counts["events_dropped"] = self.counts.get("events_dropped", 0) + 1
                return
            current = _span.get()
            self.events.append({
                "seq": len(self.events) + 1, "at_ms": round(self.now_ms(), 3), "level": level,
                "span_name": span_name or (current.name if current else None), "logger": logger,
                "message": redact.text(message), "detail": redact.mapping(detail) if detail else None,
            })

    # ── called by the code that owns the run ───────────────────────────
    def finish(self, outcome: str, *, error: BaseException | None = None, code: str | None = None,
               http_status: int | None = None) -> None:
        """Set the outcome explicitly (RETRYING, SUPERSEDED, REJECTED, …). The run is written on exit."""
        self.outcome = outcome
        if http_status is not None:
            self.http_status = http_status
        if code:
            self.error_code = code
        if error is not None:
            self.set_error(error)

    def set_error(self, error: BaseException, code: str | None = None) -> None:
        self.error_type = type(error).__name__
        self.error_code = code or self.error_code or _classify(error)
        self.failure_reason = redact.error(error)

    def label(self, **labels: Any) -> None:
        self.labels.update({k: redact.value(v) for k, v in labels.items()})

    def count(self, **n: float) -> None:
        with self._lock:
            for k, v in n.items():
                self.counts[k] = round(self.counts.get(k, 0) + v, 3)

    def set_subject(self, subject_type: str, subject_id: str) -> None:
        self.subject_type, self.subject_id = subject_type, str(subject_id)[:100]

    def finalise(self) -> None:
        self.closed = True
        self.duration_ms = round(self.now_ms(), 3)
        if self.outcome is None:
            self.outcome = "SUCCEEDED"
        if self.outcome == "SUCCEEDED" and any(not names.REASONS.get(d["reason"], names.Reason(False, "")).cost_only
                                               for d in self.degradations):
            self.outcome = "SUCCEEDED_DEGRADED"

    @property
    def queue_wait_ms(self) -> float | None:
        if self.queued_at is None:
            return None
        return max(round((self.started_at - self.queued_at).total_seconds() * 1000, 3), 0.0)

    @property
    def failure_fingerprint(self) -> str | None:
        if self.outcome in SUCCESS or self.outcome in (None, "RUNNING", "CANCELLED") or not (
                self.error_type or self.error_code):
            return None
        return fingerprint(self.error_stage, self.error_code, self.error_type, self.failure_reason)


def _classify(error: BaseException) -> str:
    name = type(error).__name__
    if "Timeout" in name:
        return "TIMEOUT"
    if name.startswith("Postgres") or "asyncpg" in type(error).__module__:
        return "DB_ERROR"
    return "UNEXPECTED"


def _warn_once(key: str, **kw: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(key, **kw)


# ── public API ─────────────────────────────────────────────────────────

class run:  # noqa: N801  (used like a function: `async with run(...)`)
    """Starts a run for one attempt at one unit of work. Never raises; never swallows the work's exception."""

    def __init__(self, kind: str, name: str, *, subject: tuple[str, str] | None = None, attempt: int = 1,
                 queued_at: datetime | None = None, tenant: str | None = None, labels: dict[str, Any] | None = None,
                 upstream: str | None = None, insert_at_start: bool = True, queued_by_db: bool = False) -> None:
        """`queued_by_db`: `queued_at` came from the DB clock (e.g. pending_work.next_attempt_at)."""
        self.kind, self.name, self.subject, self.attempt = kind, name, subject, attempt
        self.queued_at = to_local(queued_at) if queued_by_db and queued_at else queued_at
        self.tenant, self.labels = tenant, labels
        self.upstream, self.insert_at_start = upstream, insert_at_start
        self._token: Any = None
        self.run: Run | None = None

    async def __aenter__(self) -> Run:
        from app.ops.tracing import recorder

        parent = _run.get()
        r = Run(kind=self.kind, name=self.name[:150], service=recorder.service(), attempt=max(self.attempt, 1),
                queued_at=self.queued_at, tenant_id=self.tenant, parent_run_id=parent.run_id if parent else None)
        if not enabled():
            r.recorded = False
            self.run = r
            return r
        if self.subject:
            r.set_subject(*self.subject)
        if self.labels:
            r.label(**self.labels)
        up = parse_traceparent(self.upstream) if self.upstream else _upstream.get()
        if up:
            r.upstream_trace, r.upstream_service = up
        self.run = r
        self._token = _run.set(r)
        if self.insert_at_start:
            recorder.started(r)
        return r

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        from app.ops.tracing import recorder

        r = self.run
        if self._token is not None:
            _run.reset(self._token)
        if r is None or not r.recorded:
            return
        try:
            if exc is not None and r.outcome is None:
                import asyncio

                r.outcome = "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "FAILED"
                r.set_error(exc)
            r.finalise()
            recorder.finished(r)
        except Exception as e:  # never raise into the work
            _warn_once("ops.finish_failed", error_class=type(e).__name__)


class span:  # noqa: N801  (used like a function: `with span(...)`)
    """Times a block as a child of the current span. Outside a run it does nothing."""

    def __init__(self, name: str, *, target: str | None = None, **attrs: Any) -> None:
        self.name, self.target, self.attrs = name, target, attrs
        self.rec: SpanRec | None = None
        self._token: Any = None
        self._t: float = 0.0
        self._run: Run | None = None

    def __enter__(self) -> "span":
        r = _run.get()
        if r is None:
            return self
        meta = names.SPANS.get(self.name)
        if meta is None:
            _warn_once("ops.unregistered_span", span=self.name)
            meta = names.SpanName(3, "internal", "")
        parent = _span.get()
        self._run, self._t = r, time.perf_counter()
        self.rec = SpanRec(span_id=r.next_span_id(), parent_id=parent.span_id if parent else None, name=self.name,
                           level=max(meta.level, parent.level if parent else 1), kind=meta.kind,
                           start_ms=round((self._t - r.t0) * 1000, 3), target=self.target,
                           attrs=redact.mapping(self.attrs) if self.attrs else {})
        self._token = _span.set(self.rec)
        if meta.level == 1 and parent is None:
            r.current_stage = self.name
            if r.inserted and not r._stage_pending:  # trailing update: the writer writes the stage current then
                from app.ops.tracing import recorder

                r._stage_pending = True
                recorder.stage(r)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        if self.rec is None or self._run is None:
            return
        try:
            _span.reset(self._token)
            rec, r = self.rec, self._run
            rec.duration_ms = round((time.perf_counter() - self._t) * 1000, 3)
            if exc is not None:
                rec.outcome, rec.error = "failed", redact.error(exc)
                if rec.level == 1 and r.error_stage is None:
                    r.error_stage = rec.name  # inner stages exit first: the innermost failed L1 wins
            r.add_span(rec)
        except Exception as e:  # never raise into the work
            _warn_once("ops.span_failed", error_class=type(e).__name__)

    def annotate(self, **attrs: Any) -> None:
        if self.rec is not None:
            self.rec.attrs.update(redact.mapping(attrs))

    def fail(self, error: str) -> None:
        """Mark this span failed without an exception (e.g. a model call that returned an error outcome)."""
        if self.rec is not None:
            self.rec.outcome, self.rec.error = "failed", redact.text(error)


def record_span(name: str, *, started_perf: float, duration_ms: float, outcome: str = "ok",
                target: str | None = None, error: str | None = None, **attrs: Any) -> None:
    """A span measured elsewhere (the DB hook gets the elapsed time after the statement)."""
    r = _run.get()
    if r is None:
        return
    meta = names.SPANS.get(name) or names.SpanName(3, "internal", "")
    parent = _span.get()
    r.add_span(SpanRec(span_id=r.next_span_id(), parent_id=parent.span_id if parent else None, name=name,
                       level=max(meta.level, parent.level if parent else 1), kind=meta.kind,
                       start_ms=round((started_perf - r.t0) * 1000, 3), duration_ms=round(duration_ms, 3),
                       outcome=outcome, target=target, attrs=attrs, error=error))


def current_run() -> Run | None:
    return _run.get()


def current_run_id() -> str | None:
    r = _run.get()
    return r.run_id if r else None


def annotate(**attrs: Any) -> None:
    s = _span.get()
    if s is not None:
        s.attrs.update(redact.mapping(attrs))


def count(**n: float) -> None:
    r = _run.get()
    if r is not None:
        r.count(**n)
        annotate(**n)


def label(**labels: Any) -> None:
    r = _run.get()
    if r is not None:
        r.label(**labels)


def tenant(cafe_id: str | None) -> None:
    r = _run.get()
    if r is not None and cafe_id and r.tenant_id is None:
        r.tenant_id = str(cafe_id)


def subject(subject_type: str, subject_id: str) -> None:
    r = _run.get()
    if r is not None and r.subject_id is None:
        r.set_subject(subject_type, subject_id)


def degraded(reason: str, **detail: Any) -> None:
    r = _run.get()
    if r is None:
        return
    if reason not in names.REASONS:
        _warn_once("ops.unregistered_reason", reason=reason)
    s = _span.get()
    entry = {"reason": reason, "at_ms": round(r.now_ms(), 3), "span": s.name if s else None, **redact.mapping(detail)}
    with r._lock:
        r.degradations.append(entry)
    if s is not None and not names.REASONS.get(reason, names.Reason(False, "")).cost_only:
        s.outcome = "degraded"
    r.add_event("DEGRADED", reason, detail=detail or None)


def error_info(*, code: str | None = None, error: BaseException | None = None) -> None:
    """The app's own error code for the current run (set by the API error handlers)."""
    r = _run.get()
    if r is None:
        return
    if error is not None:
        r.set_error(error, code)
    elif code:
        r.error_code = code


def set_input_size(value: float, unit: str) -> None:
    r = _run.get()
    if r is not None:
        r.input_size, r.input_size_unit = value, unit


# ── W3C trace context (PDS §16.2; replaces the request_id the monitoring doc promised) ──

def traceparent() -> str | None:
    r = _run.get()
    if r is None:
        return None
    s = _span.get()
    return f"00-{uuid.UUID(r.run_id).hex}-{(s.span_id if s else 0):016x}-01"


def parse_traceparent(header: str | None, service: str | None = None) -> tuple[str, str | None] | None:
    parts = (header or "").split("-")
    if len(parts) != 4 or len(parts[1]) != 32:
        return None
    try:
        int(parts[1], 16)
    except ValueError:
        return None
    return parts[1], service


def set_upstream(header: str | None, service: str | None) -> Any:
    """For an entry point that hands work to a background task: runs started in that task link back."""
    return _upstream.set(parse_traceparent(header, service))


def reset_upstream(token: Any) -> None:
    _upstream.reset(token)


def trace_id_to_run_id(trace: str) -> str:
    return str(uuid.UUID(hex=trace))


