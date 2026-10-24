"""Scripted LLM for tests and evals. Responses are matched by a substring of the prompt's last customer line."""

import json
from collections.abc import Callable
from typing import Any

from app.integrations.llm.base import LlmResult

Responder = Callable[[str, str], dict[str, Any] | str | None]


class StubLlm:
    provider = "stub"

    def __init__(self, responder: Responder | None = None, latency_ms: int = 5) -> None:
        self.responder = responder
        self.latency_ms = latency_ms
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any], timeout_s: float,
                       max_output_tokens: int) -> LlmResult:
        self.calls.append({"model": model, "system": system, "prompt": prompt})
        out = self.responder(system, prompt) if self.responder else None
        if out is None:
            out = {"intent": "unclear", "cart_ops": [], "allergen_question": None,
                   "needs_human": {"flag": False, "reason": None}, "clarify": "Could you say that again?", "reply": ""}
        if out == "timeout":
            return LlmResult(outcome="timeout", provider=self.provider, model=model, latency_ms=self.latency_ms)
        if out == "error":
            return LlmResult(outcome="error", provider=self.provider, model=model, latency_ms=self.latency_ms)
        raw = out if isinstance(out, str) else json.dumps(out)
        try:
            data = json.loads(raw)
        except ValueError:
            return LlmResult(outcome="invalid_output", provider=self.provider, model=model, raw=raw,
                             latency_ms=self.latency_ms)
        return LlmResult(outcome="ok", provider=self.provider, model=model, data=data, raw=raw,
                         input_tokens=len(system + prompt) // 4, output_tokens=len(raw) // 4,
                         latency_ms=self.latency_ms)
