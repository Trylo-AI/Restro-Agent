"""LLM_BACKEND=dashscope in the app: refused in prod or without a key, quota exhaustion reported as an outcome
llm_calls accepts, and free-tier calls priced at zero (so the spend kill switch isn't tripped by tests)."""

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.integrations import llm
from app.integrations.llm import pricing
from app.integrations.llm.dashscope import DashScopeClient


@pytest.fixture(autouse=True)
def fresh_client():
    llm.set_client(None)
    yield
    llm.set_client(None)


def test_refused_in_prod_and_without_a_key(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_backend", "dashscope")
    monkeypatch.setattr(s, "dashscope_api_key", SecretStr("test-key"))
    monkeypatch.setattr(s, "app_env", "prod")
    with pytest.raises(RuntimeError, match="not prod"):
        llm.client()
    monkeypatch.setattr(s, "app_env", "local")
    monkeypatch.setattr(s, "dashscope_api_key", None)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        llm.client()


def test_selected_by_backend(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_backend", "dashscope")
    monkeypatch.setattr(s, "dashscope_api_key", SecretStr("test-key"))
    monkeypatch.setattr(s, "dashscope_chain", ["qwen3.8-flash"])
    c = llm.client()
    assert isinstance(c, DashScopeClient) and c.provider == "dashscope" and c.chain == ["qwen3.8-flash"]
    assert c.app_outcomes


async def test_all_models_exhausted_is_an_error_in_the_app():
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "AllocationQuota.FreeTierOnly", "message": "free tier"}})

    c = DashScopeClient(api_key="t", transport=httpx.MockTransport(api), app_outcomes=True)
    r = await c.generate(model="a", system="s", prompt="p", schema={}, timeout_s=5, max_output_tokens=10)
    assert r.outcome == "error"  # llm_calls.outcome has no "quota_exhausted"; the caller retries, then hands off


def test_free_tier_models_cost_nothing():
    assert pricing.cost("qwen3.8-max-0902", 1500, 0, 80) == 0.0
    assert pricing.cost("gemini-unknown", 1500, 0, 80) > 0  # unknown models still priced as Flash
