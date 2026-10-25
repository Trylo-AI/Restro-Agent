"""Public tracing API (PDS §6.1). Business code imports only these names; never the recorder."""

from app.ops.tracing.trace import (
    annotate,
    count,
    current_run,
    current_run_id,
    degraded,
    error_info,
    label,
    parse_traceparent,
    record_span,
    reset_upstream,
    run,
    set_input_size,
    set_upstream,
    span,
    subject,
    tenant,
    to_local,
    traceparent,
)

__all__ = [
    "annotate", "count", "current_run", "current_run_id", "degraded", "error_info", "label", "parse_traceparent",
    "record_span", "reset_upstream", "run", "set_input_size", "set_upstream", "span", "subject", "tenant", "to_local", "traceparent",
]
