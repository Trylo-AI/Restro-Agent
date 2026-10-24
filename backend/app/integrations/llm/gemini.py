"""Gemini on the google-genai SDK: developer API (API key) or Vertex AI (ADC, global endpoint)."""

import asyncio
import json
import time
from typing import Any

from google import genai
from google.genai import errors, types

from app.core.config import get_settings
from app.integrations.llm.base import LlmResult


def thinking(model: str) -> types.ThinkingConfig:
    """Latency first (Phase 2 evals may revisit). Gemini 3 takes a level; 2.x takes a token budget."""
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)


class GeminiClient:
    def __init__(self, backend: str) -> None:
        s = get_settings()
        self.provider = backend
        if backend == "vertex":
            self._client = genai.Client(vertexai=True, project=s.gcp_project, location=s.gcp_location)
        else:
            if not s.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not set")
            self._client = genai.Client(api_key=s.gemini_api_key.get_secret_value())

    async def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any], timeout_s: float,
                       max_output_tokens: int) -> LlmResult:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0.2,
            max_output_tokens=max_output_tokens,
            thinking_config=thinking(model),
        )
        started = time.perf_counter()
        result = LlmResult(outcome="error", provider=self.provider, model=model)
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(model=model, contents=prompt, config=config), timeout=timeout_s)
        except TimeoutError:
            result.outcome = "timeout"
        except errors.APIError as exc:
            result.outcome = "rate_limited" if getattr(exc, "code", None) == 429 else "error"
        except Exception:  # network and SDK errors: never crash the turn
            result.outcome = "error"
        else:
            usage = response.usage_metadata
            if usage is not None:
                result.input_tokens = usage.prompt_token_count or 0
                result.cached_tokens = usage.cached_content_token_count or 0
                result.output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
            result.raw = response.text or ""
            try:
                parsed = json.loads(result.raw)
                result.data = parsed if isinstance(parsed, dict) else None
                result.outcome = "ok" if result.data is not None else "invalid_output"
            except ValueError:
                result.outcome = "invalid_output"
        result.latency_ms = round((time.perf_counter() - started) * 1000)
        return result
