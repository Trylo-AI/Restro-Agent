"""LLM adapters. Only this package imports the SDK; only domain/ai_agent calls it.

`developer_api` (Gemini API key, synthetic data only) and `vertex` (GCP, global endpoint) share one adapter on
the google-genai SDK; `stub` returns scripted outputs for tests and evals;
`stub_menu` reads dish names and confirms, with a real-call delay, for load tests. Switch with LLM_BACKEND.
"""

from app.core.config import get_settings
from app.integrations.llm.base import LlmClient, LlmResult
from app.integrations.llm.stub import StubLlm, menu_words

_client: LlmClient | None = None


def client() -> LlmClient:
    global _client
    if _client is None:
        backend = get_settings().llm_backend
        if backend == "stub":
            _client = StubLlm()
        elif backend == "stub_menu":
            _client = StubLlm(menu_words, latency_ms=get_settings().llm_stub_delay_ms, sleep=True)
        else:
            from app.integrations.llm.gemini import GeminiClient

            _client = GeminiClient(backend)
    return _client


def set_client(c: LlmClient | None) -> None:
    global _client
    _client = c


__all__ = ["LlmClient", "LlmResult", "client", "set_client"]
