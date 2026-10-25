"""Eval-only Alibaba client (evals/dashscope.py) against a fake API: fallback when a model's free quota is gone,
adapting to rejected parameters, JSON mode when a model takes no schema, and reading the JSON out of a reply."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evals"))

from dashscope import DashScopeClient, extract_json  # noqa: E402

SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}}
OK = {"choices": [{"message": {"content": '{"intent": "add_items"}'}}],
      "usage": {"prompt_tokens": 1000, "completion_tokens": 50}}


class FakeApi:
    """Answers per model: a dict body (200) or (status, error) tuples; records every request."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        answer = self.answers[body["model"]]
        if callable(answer):
            answer = answer(body)
        if isinstance(answer, tuple):
            return httpx.Response(answer[0], json={"error": answer[1]})
        return httpx.Response(200, json=answer)


async def _gen(client, model):
    return await client.generate(model=model, system="sys", prompt="2 cold coffee", schema=SCHEMA, timeout_s=5,
                                 max_output_tokens=100)


async def test_exhausted_model_hands_over_to_the_next():
    api = FakeApi({"a": (403, {"code": "AllocationQuota.FreeTierOnly", "message": "The free tier quota is exhausted"}),
                   "b": OK})
    client = DashScopeClient(chain=["a", "b"], transport=httpx.MockTransport(api))
    r = await _gen(client, "a")
    assert r.ok and r.model == "b" and client.exhausted == {"a"}
    await _gen(client, "a")
    assert [b["model"] for b in api.requests] == ["a", "b", "b"]  # an exhausted model isn't tried again
    assert client.tokens_used == {"b": 2100}


async def test_all_exhausted_is_reported_not_scored():
    api = FakeApi({"a": (403, {"code": "AllocationQuota.FreeTierOnly", "message": "free tier"})})
    r = await _gen(DashScopeClient(transport=httpx.MockTransport(api)), "a")
    assert r.outcome == "quota_exhausted" and not r.ok


async def test_rejected_parameter_is_dropped_and_remembered():
    def kimi(body):
        if "temperature" in body:
            return 400, {"code": "invalid_parameter_error",
                         "message": "InternalError.Algo.InvalidParameter: Parameter 'temperature'=0.2 is not supported"}
        return OK
    api = FakeApi({"kimi": kimi})
    client = DashScopeClient(transport=httpx.MockTransport(api))
    assert (await _gen(client, "kimi")).ok
    assert (await _gen(client, "kimi")).ok
    assert client.dropped == {"kimi": {"temperature"}} and len(api.requests) == 3  # one retry, then remembered


async def test_no_schema_support_falls_back_to_json_mode_with_the_schema_in_the_prompt():
    def ds(body):
        if body["response_format"]["type"] == "json_schema":
            return 400, {"code": "InvalidParameter", "message": "response_format json_schema is not supported"}
        return OK
    api = FakeApi({"ds": ds})
    client = DashScopeClient(transport=httpx.MockTransport(api))
    assert (await _gen(client, "ds")).ok
    assert client.schema_mode == {"ds": "json_object"}
    assert '"intent"' in api.requests[-1]["messages"][0]["content"]


async def test_rate_limit_is_not_exhaustion():
    api = FakeApi({"a": (429, {"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded"})})
    client = DashScopeClient(transport=httpx.MockTransport(api))
    r = await _gen(client, "a")
    assert r.outcome == "rate_limited" and client.exhausted == set()


@pytest.mark.parametrize("text", ['{"intent": "x"}', '```json\n{"intent": "x"}\n```',
                                  '<think>hmm</think>{"intent": "x"}', 'Sure! {"intent": "x"} done'])
def test_extract_json(text):
    assert extract_json(text) == {"intent": "x"}


def test_extract_json_rejects_non_objects():
    assert extract_json("[1, 2]") is None and extract_json("no json") is None
