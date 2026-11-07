"""The single place the LLM is called (docs/backend.md#ai-agent, #failures).

One structured call; on error, timeout, 429 or invalid output, one retry on the other model. Every attempt
(failures included) writes `llm_calls` with its cost. Kill switch: `cheap` → only the cheaper model, no retry
on the dearer one; `handoff` → no call at all (the caller hands the turn to staff).
A `recommend` call passes its own model, a shorter timeout and `retry=False`: one attempt, then code picks.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import devhooks
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories import llm_calls
from app.db.tx import conn
from app.integrations import llm
from app.integrations.llm import pricing
from app.integrations.llm.base import LlmResult
from app.ops import tracing

log = get_logger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parents[4] / "contracts" / "llm_output.schema.json"
_auto_kill: str | None = None


def schema(name: str) -> dict[str, Any]:
    defs = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["$defs"]
    return dict(defs[name])


def kill_switch() -> str:
    env = get_settings().llm_kill_switch
    return env if env != "off" else (_auto_kill or "off")


def set_auto_kill(mode: str | None) -> None:
    """Set by the llm_spend_check job when today's spend passes the limit."""
    global _auto_kill
    _auto_kill = mode


def models(cafe_model: str | None) -> list[str]:
    s = get_settings()
    primary = cafe_model or s.llm_model_primary
    other = s.llm_model_fallback if primary != s.llm_model_fallback else s.llm_model_primary
    if kill_switch() == "cheap":
        return [s.llm_model_fallback]  # the fallback is the cheaper model (Flash-Lite)
    return [primary, other] if other != primary else [primary]


@dataclass
class Attempted:
    result: LlmResult
    call_id: str


async def call(*, cafe_id: str, cafe_model: str | None, purpose: str, system: str, prompt: str, schema_name: str,
               max_output_tokens: int, conversation_id: str | None = None, message_id: str | None = None,
               order_id: str | None = None, note_id: str | None = None,
               trace_suffix: str = "", timeout_s: float | None = None, retry: bool = True,
               models_override: list[str] | None = None, fail_reason: str = "llm_handoff") -> Attempted | None:
    """Returns the first good attempt, or None when every attempt failed (caller hands off, or falls back).

    `models_override` replaces the café / primary chain (the `cheap` kill switch still wins); `retry=False` keeps
    only the first model; `fail_reason` is the degradation recorded when nothing worked.
    """
    s = get_settings()
    out_schema = schema(schema_name)
    fault = await devhooks.consume_fault("llm")
    last: Attempted | None = None
    if kill_switch() != "off":
        tracing.degraded("llm_kill_switch", mode=kill_switch())
    chain = models_override if models_override and kill_switch() != "cheap" else models(cafe_model)
    if not retry:
        chain = chain[:1]
    with tracing.span("agent.call", purpose=purpose):
        for i, model in enumerate(chain):
            if i > 0:
                tracing.degraded("llm_fallback_model", model=model, first_outcome=last.result.outcome if last else None)
            with tracing.span("llm.call", target=model, purpose=purpose, **{"llm.model": model}) as sp:
                if i == 0 and fault == "llm_fail":
                    result = LlmResult(outcome="error", provider=llm.client().provider, model=model)
                else:
                    result = await llm.client().generate(model=model, system=system, prompt=prompt, schema=out_schema,
                                                         timeout_s=timeout_s or s.llm_timeout_seconds,
                                                         max_output_tokens=max_output_tokens)
                    if i == 0 and fault == "llm_invalid":
                        result.outcome, result.data = "invalid_output", None
                sp.annotate(**{"llm.provider": result.provider, "outcome": result.outcome,
                               "tokens_in": result.input_tokens, "tokens_out": result.output_tokens,
                               "tokens_cached": result.cached_tokens, "latency_ms": result.latency_ms})
                if not result.ok:
                    sp.fail(result.outcome)
                if result.outcome == "rate_limited":
                    tracing.degraded("llm_rate_limited", model=model)
            tracing.count(llm_calls=1, tokens_in=result.input_tokens or 0, tokens_out=result.output_tokens or 0)
            tracing.label(llm_model=model)
            cost = pricing.cost(model, result.input_tokens, result.cached_tokens, result.output_tokens)
            async with conn() as c:
                call_id = await llm_calls.insert(
                    c, cafe_id, provider=result.provider, model=model, purpose=purpose, conversation_id=conversation_id,
                    message_id=message_id, order_id=order_id, note_id=note_id, input_tokens=result.input_tokens,
                    cached_tokens=result.cached_tokens, output_tokens=result.output_tokens, cost=cost,
                    currency=pricing.CURRENCY, pricing_version=pricing.PRICING_VERSION, latency_ms=result.latency_ms,
                    outcome=result.outcome,
                )
            sp.annotate(llm_call_id=call_id)
            last = Attempted(result, call_id)
            log.info("llm_call", cafe_id=cafe_id, conversation_id=conversation_id, purpose=purpose, model=model,
                     outcome=result.outcome, latency_ms=result.latency_ms)
            if s.dev_traces and s.is_local:
                await trace(cafe_id, conversation_id, call_id, purpose, len(system) + len(prompt), trace_suffix or prompt,
                            result.raw, result.data, [])
            if result.ok:
                return last
    tracing.degraded(fail_reason, purpose=purpose)
    return None


async def trace(cafe_id: str, conversation_id: str | None, call_id: str, purpose: str, prompt_chars: int, suffix: str,
                raw: str, parsed: Any, guardrails: list[Any]) -> None:
    async with conn() as c:
        await llm_calls.insert_trace(c, cafe_id, conversation_id=conversation_id, llm_call_id=call_id, purpose=purpose,
                                     prompt_chars=prompt_chars, suffix=suffix, raw_output=raw, parsed=parsed,
                                     guardrails=guardrails)


async def add_guardrails_to_trace(cafe_id: str, call_id: str, guardrails: list[Any]) -> None:
    """Record what the guardrails changed on the most recent trace (local dev only)."""
    s = get_settings()
    if not (s.dev_traces and s.is_local) or not guardrails:
        return
    async with conn() as c:
        await llm_calls.update_trace_guardrails(c, cafe_id, call_id, guardrails)
