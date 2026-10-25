from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LlmResult:
    outcome: str                      # ok · invalid_output · timeout · rate_limited · error
    provider: str                     # developer_api · vertex · stub · dashscope (testing)
    model: str
    data: dict[str, Any] | None = None
    raw: str = ""
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome == "ok" and self.data is not None


class LlmClient(Protocol):
    provider: str

    async def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any], timeout_s: float,
                       max_output_tokens: int) -> LlmResult:
        """One structured call. Never raises: failures come back as an outcome."""
        ...
