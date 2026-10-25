"""The registry: every span name and degradation reason (docs/OPS_PLAN.md §3). A name not listed here is a test
failure (tests/unit/test_ops_registry.py), never a new row in a chart. Names are dotted, lowercase and stable;
ids and values go in attrs, never in a name.
"""

from typing import NamedTuple


class SpanName(NamedTuple):
    level: int  # 1 stage, 2 step, 3 call
    kind: str  # internal | db | http_out | llm | queue
    description: str


class Reason(NamedTuple):
    cost_only: bool  # counted, but doesn't turn the run amber
    description: str


SPANS: dict[str, SpanName] = {
    # Webhook (kind=message)
    "webhook.verify": SpanName(1, "internal", "Signature check on the raw body"),
    "webhook.store": SpanName(1, "internal", "Inbound message: customer, conversation, inbound row, pending_work"),
    "webhook.status": SpanName(1, "internal", "Status callback applied to wa_messages"),
    "webhook.signal": SpanName(1, "internal", "Wake-up signal and wa-ack task after commit"),
    # Turn (kind=job, name=turn)
    "turn.load": SpanName(1, "internal", "Unprocessed messages and turn context"),
    "turn.route": SpanName(1, "internal", "Routing, flows and the AI agent (no transaction open)"),
    "turn.commit": SpanName(1, "internal", "Fenced transaction: state, orders, outbound rows, processed_at"),
    "turn.send": SpanName(1, "internal", "Outbound steps sent after commit"),
    "turn.after": SpanName(1, "internal", "After-commit hooks"),
    "turn.dead_letter": SpanName(1, "internal", "Dead-letter message and handoff"),
    "turn.finish": SpanName(1, "internal", "pending_work row done, or the failure recorded for a retry"),
    "turn.devhooks": SpanName(1, "internal", "Simulator fault check and event push (local only; never in the cloud)"),
    # Tasks, timers, jobs
    "task.handle": SpanName(1, "internal", "A task or timer handler"),
    "job.handle": SpanName(1, "internal", "A scheduled job"),
    # Staff REST
    "request.handle": SpanName(1, "internal", "A staff API request"),
    # Steps
    "agent.call": SpanName(2, "internal", "One structured model call with its retry on the other model"),
    "orders.transition": SpanName(2, "internal", "Order state change with optimistic locking"),
    "outbound.deliver": SpanName(2, "internal", "Claim, send and record one outbound step"),
    "db.tx": SpanName(2, "db", "A database transaction"),
    # Calls
    "db.query": SpanName(3, "db", "One SQL statement"),
    "db.acquire": SpanName(3, "db", "Waiting for a pooled connection (only recorded when ≥ 5 ms)"),
    "llm.call": SpanName(3, "llm", "One Gemini request"),
    "wa.send": SpanName(3, "http_out", "One WhatsApp send (Meta or the simulator)"),
    "wa.mark_read": SpanName(3, "http_out", "Read receipt and typing indicator"),
    "queue.publish": SpanName(3, "queue", "Wake-up signal for the worker"),
    "queue.enqueue": SpanName(3, "queue", "Task or timer enqueued"),
}

REASONS: dict[str, Reason] = {
    "llm_fallback_model": Reason(False, "The primary model failed; the other model answered"),
    "llm_rate_limited": Reason(True, "Gemini returned 429"),
    "llm_handoff": Reason(False, "Both model attempts failed; the turn went to staff (system_error)"),
    "llm_kill_switch": Reason(False, "The kill switch limited or skipped the model"),
    "guardrail_reply_dropped": Reason(False, "The model's text held an amount and was dropped"),
    "cancel_draft_fallback": Reason(False, "The AI cancellation text was rejected; the fixed text was sent"),
    "send_ambiguous": Reason(False, "A send timed out after leaving; send_check will resolve it"),
    "send_retry_429": Reason(True, "WhatsApp asked us to retry; retried with backoff"),
    "send_template_fallback": Reason(False, "The 24-hour window was closed; a template went instead"),
    "sold_out_dropped": Reason(False, "A dish sold out before Confirm and was removed with a notice"),
    "publish_failed_sweep": Reason(False, "The wake-up signal failed; stuck_inbound_check will re-signal"),
    "profile_note_failed": Reason(False, "A staff comment couldn't be read; the profile is unchanged"),
    "ack_failed": Reason(True, "Read receipt / typing indicator not shown"),
}

LEVELS = {1: "stage", 2: "step", 3: "call"}
