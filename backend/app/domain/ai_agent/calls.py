"""The single place the LLM is called (docs/backend.md#ai-agent, #failures).

One structured call; on error, timeout, 429 or invalid output, one retry on the other model. Every attempt
(failures included) writes `llm_calls` with its cost. Kill switch: `cheap` → only the cheaper model, no retry
on the dearer one; `handoff` → no call at all (the caller hands the turn to staff).
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
               trace_suffix: str = "") -> Attempted | None:
    """Returns the first good attempt, or None when every attempt failed (caller hands off)."""
    s = get_settings()
    out_schema = schema(schema_name)
    fault = await devhooks.consume_fault("llm")
    last: Attempted | None = None
    for i, model in enumerate(models(cafe_model)):
        if i == 0 and fault == "llm_fail":
            result = LlmResult(outcome="error", provider=llm.client().provider, model=model)
        else:
            result = await llm.client().generate(model=model, system=system, prompt=prompt, schema=out_schema,
                                                 timeout_s=s.llm_timeout_seconds, max_output_tokens=max_output_tokens)
            if i == 0 and fault == "llm_invalid":
                result.outcome, result.data = "invalid_output", None
        cost = pricing.cost(model, result.input_tokens, result.cached_tokens, result.output_tokens)
        async with conn() as c:
            call_id = await llm_calls.insert(
                c, cafe_id, provider=result.provider, model=model, purpose=purpose, conversation_id=conversation_id,
                message_id=message_id, order_id=order_id, note_id=note_id, input_tokens=result.input_tokens,
                cached_tokens=result.cached_tokens, output_tokens=result.output_tokens, cost=cost,
                currency=pricing.CURRENCY, pricing_version=pricing.PRICING_VERSION, latency_ms=result.latency_ms,
                outcome=result.outcome,
            )
        last = Attempted(result, call_id)
        log.info("llm_call", cafe_id=cafe_id, conversation_id=conversation_id, purpose=purpose, model=model,
                 outcome=result.outcome, latency_ms=result.latency_ms)
        if s.dev_traces and s.is_local:
            await trace(cafe_id, conversation_id, call_id, purpose, len(system) + len(prompt), trace_suffix or prompt,
                        result.raw, result.data, [])
        if result.ok:
            return last
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
