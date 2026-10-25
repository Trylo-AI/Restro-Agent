"""Scripted LLM for tests, evals and load tests. Responses are matched by a substring of the prompt's last customer line."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from app.integrations.llm.base import LlmResult

Responder = Callable[[str, str], dict[str, Any] | str | None]


class StubLlm:
    provider = "stub"

    def __init__(self, responder: Responder | None = None, latency_ms: int = 5, sleep: bool = False) -> None:
        self.responder = responder
        self.latency_ms = latency_ms
        self.sleep = sleep  # load tests: hold the turn as long as a real call would
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any], timeout_s: float,
                       max_output_tokens: int) -> LlmResult:
        if self.sleep:
            await asyncio.sleep(self.latency_ms / 1000)
        else:
            self.calls.append({"model": model, "system": system, "prompt": prompt})  # unbounded: tests only
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


_CONFIRM = re.compile(r"^(confirm|that'?s all|thats it|done|bas)\b", re.I)
_LINE = re.compile(r"^(\d{1,2})\s+(.+)$")


def menu_words(_system: str, prompt: str) -> dict[str, Any] | None:
    """LLM_BACKEND=stub_menu (load tests): "2 Cold Coffee and 1 Chocolate Brownie" adds those dishes by name,
    "that's all" confirms; anything else falls back to the unclear reply. Names resolve through the same
    guardrails as a real reply (item_ref by name)."""
    text = prompt.rsplit("CUSTOMER NOW SAYS:\n", 1)[-1].strip()
    if _CONFIRM.match(text):
        return {"intent": "confirm", "cart_ops": [], "allergen_question": None,
                "needs_human": {"flag": False, "reason": None}, "clarify": None, "reply": ""}
    ops = []
    for part in re.split(r",|\band\b", text):
        m = _LINE.match(part.strip())
        if m:
            ops.append({"op": "add", "item_ref": m.group(2).strip(), "qty": int(m.group(1)), "note": None})
    if not ops:
        return None
    return {"intent": "add_items", "cart_ops": ops, "allergen_question": None,
            "needs_human": {"flag": False, "reason": None}, "clarify": None, "reply": "Done! Anything else?"}
