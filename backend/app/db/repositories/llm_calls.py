from typing import Any

import asyncpg


async def insert(conn: asyncpg.Connection, cafe_id: str, *, provider: str, model: str, purpose: str,
                 conversation_id: str | None, message_id: str | None, order_id: str | None, note_id: str | None,
                 input_tokens: int, cached_tokens: int, output_tokens: int, cost: float, currency: str,
                 pricing_version: str, latency_ms: int, outcome: str) -> str:
    return str(await conn.fetchval(
        """insert into llm_calls (cafe_id, provider, model, purpose, conversation_id, message_id, order_id, note_id,
                                  input_tokens, cached_tokens, output_tokens, cost, currency, pricing_version,
                                  latency_ms, outcome)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16) returning id::text""",
        cafe_id, provider, model, purpose, conversation_id, message_id, order_id, note_id, input_tokens, cached_tokens,
        output_tokens, cost, currency, pricing_version, latency_ms, outcome,
    ))


async def spend_today_usd(conn: asyncpg.Connection) -> float:
    """All cafés, UTC day (cross-café job query for the kill switch)."""
    return float(await conn.fetchval(
        "select coalesce(sum(cost), 0) from llm_calls where currency = 'USD' and created_at >= date_trunc('day', now())"))


async def insert_trace(conn: asyncpg.Connection, cafe_id: str, *, conversation_id: str | None, llm_call_id: str,
                       purpose: str, prompt_chars: int, suffix: str, raw_output: str, parsed: Any,
                       guardrails: list[Any]) -> None:
    """dev_turn_traces exists only in local dev databases (supabase/dev/); elsewhere this is a no-op."""
    try:
        await conn.execute(
            """insert into dev_turn_traces (cafe_id, conversation_id, llm_call_id, purpose, prompt_chars, suffix,
                                            raw_output, parsed, guardrails)
               values ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            cafe_id, conversation_id, llm_call_id, purpose, prompt_chars, suffix[:6000], raw_output[:6000], parsed,
            guardrails,
        )
    except asyncpg.UndefinedTableError:
        return


async def update_trace_guardrails(conn: asyncpg.Connection, cafe_id: str, llm_call_id: str, guardrails: list[Any]) -> None:
    try:
        await conn.execute("update dev_turn_traces set guardrails = $3 where cafe_id = $1 and llm_call_id = $2",
                           cafe_id, llm_call_id, guardrails)
    except asyncpg.UndefinedTableError:
        return
