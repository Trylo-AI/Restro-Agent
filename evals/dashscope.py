"""Alibaba Cloud Model Studio (DashScope, Singapore) for evals only: synthetic cases, free-tier quota.

Never imported by the app: production stays on Gemini / Vertex AI (docs/decisions.md). The OpenAI-compatible
chat endpoint, JSON schema output when the model accepts it, plain JSON mode with the schema in the prompt
when it doesn't. When a model's free quota is used up (Stop-on-Exhaust), the call moves to the next model in
the chain and the result says which model answered. The key comes from DASHSCOPE_API_KEY and is never logged.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.integrations.llm.base import LlmResult

BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
ENV_FILE = Path(__file__).resolve().parents[1] / "backend" / ".env"

# Error codes that mean "this model's free quota is gone" (Stop-on-Exhaust) or the model can't be used at all.
EXHAUSTED = re.compile(r"FreeTierOnly|free tier|quota.*exhaust|exhaust.*quota|Arrearage|insufficient", re.I)
UNUSABLE = re.compile(r"model_not_found|ModelNotFound|does not exist|AccessDenied|not supported|Unpurchased", re.I)


def api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("DASHSCOPE_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("DASHSCOPE_API_KEY is missing in backend/.env")
    return key


def extract_json(text: str) -> dict[str, Any] | None:
    """The object in a reply, tolerating ```json fences or a <think> block before it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


@dataclass
class DashScopeClient:
    """LlmClient for the eval runner. `chain` = models to fall back to, in order, when one is exhausted."""

    chain: list[str] = field(default_factory=list)
    transport: httpx.AsyncBaseTransport | None = None
    provider: str = "dashscope"
    exhausted: set[str] = field(default_factory=set)
    schema_mode: dict[str, str] = field(default_factory=dict)  # model → "json_schema" | "json_object"
    tokens_used: dict[str, int] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)
    unusable: set[str] = field(default_factory=set)
    dropped: dict[str, set[str]] = field(default_factory=dict)  # model → request fields it rejected

    def __post_init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=BASE_URL, transport=self.transport, timeout=60,
                                         headers={"Authorization": f"Bearer {api_key() if self.transport is None else 'test'}"})

    def body(self, model: str, system: str, prompt: str, schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        mode = self.schema_mode.get(model, "json_schema")
        if mode == "json_schema":
            fmt: dict[str, Any] = {"type": "json_schema", "json_schema": {"name": "turn", "schema": schema, "strict": True}}
        else:
            fmt = {"type": "json_object"}
            system = system + "\n\nReply with one JSON object only, matching this JSON schema:\n" + json.dumps(schema)
        body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens, "response_format": fmt,
                "enable_thinking": False,  # Qwen3 hybrid models: answer directly (latency first, as with Gemini)
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        for name in self.dropped.get(model, set()):
            body.pop(name, None)
        return body

    async def _call(self, model: str, system: str, prompt: str, schema: dict[str, Any], timeout_s: float,
                    max_tokens: int) -> tuple[LlmResult, str]:
        """One HTTP call. Returns the result and the provider's error code ("" when fine)."""
        result = LlmResult(outcome="error", provider=self.provider, model=model)
        started = time.perf_counter()
        try:
            r = await self._client.post("/chat/completions", json=self.body(model, system, prompt, schema, max_tokens),
                                        timeout=timeout_s)
        except httpx.TimeoutException:
            result.outcome = "timeout"
            return result, "timeout"
        except httpx.HTTPError as exc:
            return result, type(exc).__name__
        finally:
            result.latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.status_code >= 400:
            err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            code = f"{r.status_code} {err.get('code') or ''} {str(err.get('message') or '')[:160]}".strip()
            if r.status_code == 429 and not EXHAUSTED.search(code):
                result.outcome = "rate_limited"
            elif EXHAUSTED.search(code):
                result.outcome = "quota_exhausted"
            return result, code
        usage = payload.get("usage") or {}
        result.input_tokens = int(usage.get("prompt_tokens") or 0)
        result.cached_tokens = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        result.output_tokens = int(usage.get("completion_tokens") or 0)
        self.tokens_used[model] = self.tokens_used.get(model, 0) + result.input_tokens + result.output_tokens
        try:
            result.raw = payload["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            result.raw = ""
        result.data = extract_json(result.raw)
        result.outcome = "ok" if result.data is not None else "invalid_output"
        return result, ""

    async def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any], timeout_s: float,
                       max_output_tokens: int) -> LlmResult:
        for candidate in [model, *[m for m in self.chain if m != model]]:
            if candidate in self.exhausted or candidate in self.unusable:
                continue
            result, code = await self._call(candidate, system, prompt, schema, timeout_s, max_output_tokens)
            for _ in range(3):  # adapt to what this model accepts, then remember it
                if not code or EXHAUSTED.search(code):
                    break
                param = re.search(r"Parameter '(\w+)'", code)
                if param and param.group(1) in ("temperature", "enable_thinking", "max_tokens"):
                    self.dropped.setdefault(candidate, set()).add(param.group(1))
                elif self.schema_mode.get(candidate, "json_schema") == "json_schema" and \
                        re.search(r"response_format|json_schema|InvalidParameter", code, re.I):
                    self.schema_mode[candidate] = "json_object"  # no schema support: the schema goes in the prompt
                else:
                    break
                result, code = await self._call(candidate, system, prompt, schema, timeout_s, max_output_tokens)
            if code:
                self.last_error[candidate] = code
            if result.outcome == "quota_exhausted":
                self.exhausted.add(candidate)
                continue  # free quota gone: the next model in the chain answers
            if code and UNUSABLE.search(code):
                self.unusable.add(candidate)
                continue
            return result
        outcome = "quota_exhausted" if model in self.exhausted else "error"
        return LlmResult(outcome=outcome, provider=self.provider, model=model)
