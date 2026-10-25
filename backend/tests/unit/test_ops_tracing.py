"""Tracing core (PDS §17): nesting, exceptions, outcomes, kill switch, registry, redaction, fingerprints, imports."""

import ast
import asyncio
import re
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.ops import tracing
from app.ops.tracing import names, recorder, redact
from app.ops.tracing.fingerprint import fingerprint, statement
from app.ops.tracing.hooks.http_in import outcome_for
from app.ops.tracing.trace import Run

APP = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[Run]:
    out: list[Run] = []
    monkeypatch.setattr(recorder, "finished", out.append)
    monkeypatch.setattr(recorder, "started", lambda r: None)
    return out


async def test_nested_spans_parent_correctly_and_fill_stage_and_dep_maps(captured: list[Run]) -> None:
    async with tracing.run("job", "turn"):
        with tracing.span("turn.load"):
            tracing.record_span("db.query", started_perf=0.0, duration_ms=12.5, target="orders")
        with tracing.span("turn.route"), tracing.span("agent.call"), tracing.span("llm.call"):
            pass
    (r,) = captured
    by = {s.name: s for s in r.spans}
    assert by["db.query"].parent_id == by["turn.load"].span_id
    assert by["llm.call"].parent_id == by["agent.call"].span_id
    assert by["agent.call"].parent_id == by["turn.route"].span_id
    assert by["turn.load"].parent_id is None and by["turn.load"].level == 1
    assert set(r.stage_ms) == {"turn.load", "turn.route"}
    assert r.dep_calls == {"db": 1, "llm": 1} and r.dep_ms["db"] == 12.5
    assert r.outcome == "SUCCEEDED"


async def test_concurrent_siblings_both_parent_to_the_caller(captured: list[Run]) -> None:
    async def child(name: str) -> None:
        with tracing.span(name):
            await asyncio.sleep(0.01)

    async with tracing.run("job", "turn"):
        with tracing.span("turn.send"):
            await asyncio.gather(child("wa.send"), child("wa.send"))
    (r,) = captured
    send = next(s for s in r.spans if s.name == "turn.send")
    kids = [s for s in r.spans if s.name == "wa.send"]
    assert len(kids) == 2 and all(k.parent_id == send.span_id for k in kids)
    assert kids[0].start_ms < kids[1].start_ms + 5 and kids[1].start_ms < kids[0].start_ms + 5  # side by side


async def test_failing_span_records_failed_and_reraises_unchanged(captured: list[Run]) -> None:
    err = ValueError("boom 9199990000123")
    with pytest.raises(ValueError) as info:
        async with tracing.run("job", "turn"):
            with tracing.span("turn.commit"), tracing.span("db.tx"):
                raise err
    assert info.value is err
    (r,) = captured
    assert r.outcome == "FAILED" and r.error_stage == "turn.commit" and r.error_type == "ValueError"
    assert "9199990000123" not in (r.failure_reason or "")
    assert all(s.outcome == "failed" for s in r.spans)
    assert r.failure_fingerprint and len(r.failure_fingerprint) == 16


async def test_explicit_outcomes_and_degradations(captured: list[Run]) -> None:
    async with tracing.run("job", "turn") as r1:
        r1.finish("RETRYING", error=RuntimeError("x"))
    async with tracing.run("job", "turn"):
        tracing.degraded("llm_fallback_model", model="m")
    async with tracing.run("job", "turn"):
        tracing.degraded("llm_rate_limited")  # cost only: stays green
    assert [r.outcome for r in captured] == ["RETRYING", "SUCCEEDED_DEGRADED", "SUCCEEDED"]
    assert captured[1].degradations[0]["reason"] == "llm_fallback_model"
    assert captured[1].events[0]["level"] == "DEGRADED"


@pytest.mark.parametrize(("status", "outcome"), [(200, "SUCCEEDED"), (302, "SUCCEEDED"), (404, "REJECTED"),
                                                 (409, "REJECTED"), (422, "REJECTED"), (408, "FAILED"),
                                                 (429, "FAILED"), (500, "FAILED")])
def test_http_status_mapping(status: int, outcome: str) -> None:
    assert outcome_for(status) == outcome


async def test_kill_switch_records_nothing(captured: list[Run], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ops_tracing_enabled", False)
    async with tracing.run("job", "turn"):
        with tracing.span("turn.load"):
            tracing.count(x=1)
        assert tracing.current_run_id() is None
    assert captured == []


async def test_spans_outside_a_run_do_nothing() -> None:
    with tracing.span("turn.load") as s:
        tracing.annotate(a=1)
        tracing.degraded("llm_handoff")
    assert s.rec is None


async def test_traceparent_links_a_child_run_to_its_parent(captured: list[Run]) -> None:
    async with tracing.run("message", "POST /webhooks/whatsapp") as parent:
        with tracing.span("webhook.signal"):
            header = tracing.traceparent()
    async with tracing.run("job", "turn", upstream=header) as child:
        pass
    assert header and header.startswith("00-") and len(header.split("-")[1]) == 32
    assert child.upstream_trace == parent.run_id.replace("-", "")


def test_redaction_of_planted_secrets() -> None:
    planted = ("key=AIzaSyA1234567890abcdef token=abc.def password=hunter2 "
               "https://x.example/f?X-Amz-Signature=deadbeef&sig=cafe a@b.com +91 99999 00001 "
               "postgresql://u:pw@host:6543/db Bearer eyJhbGci.x.y sb_secret_abcdefghijkl")
    out = redact.text(planted)
    for secret in ("AIzaSy", "abc.def", "hunter2", "deadbeef", "cafe", "a@b.com", "99999 00001", "u:pw", "eyJhbGci",
                   "sb_secret_abcdefghijkl"):
        assert secret not in out, secret
    assert redact.mapping({"text": "hi", "wa_id": "9199", "ok": 1}) == {"text": "[removed]", "wa_id": "[removed]", "ok": 1}


def test_fingerprint_groups_ids_and_splits_codes() -> None:
    a = fingerprint("turn.commit", "DB_ERROR", "UniqueViolationError", "key (id)=(1b4e28ba-2fa1-11d2-883f-0016d3cca427) 42")
    b = fingerprint("turn.commit", "DB_ERROR", "UniqueViolationError", "key (id)=(6fa459ea-ee8a-3ca4-894e-db77e160355e) 7")
    c = fingerprint("turn.commit", "TIMEOUT", "UniqueViolationError", "key (id)=(6fa459ea-ee8a-3ca4-894e-db77e160355e) 7")
    assert a == b != c


def test_statement_normalised_without_values() -> None:
    op, digest, table = statement("select * from orders where cafe_id = $1 and number = 1042 and note = 'no chilli'")
    op2, digest2, _ = statement("SELECT *  FROM orders WHERE cafe_id = $2 and number = 7 and note = 'x'")
    assert (op, table) == ("select", "orders") and digest == digest2 and op2 == "select"


def _calls(pattern: str) -> set[str]:
    rx = re.compile(pattern)
    return {m.group(1) for f in APP.rglob("*.py") for m in rx.finditer(f.read_text(encoding="utf-8"))}


def test_every_span_and_reason_used_in_code_is_registered() -> None:
    used_spans = _calls(r"""(?:tracing\.span|trace\.span|\bspan|record_span)\(\s*"([a-z_.]+)\"""")
    used_reasons = _calls(r"""degraded\(\s*"([a-z_0-9]+)\"""")
    assert used_spans - set(names.SPANS) == set()
    assert used_reasons - set(names.REASONS) == set()
    assert used_spans, "the pattern found no span calls"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_architecture_import_rules() -> None:
    business = ("app.domain", "app.api", "app.integrations")
    for f in (APP / "ops" / "tracing").rglob("*.py"):
        bad = {m for m in _imports(f) if m.startswith(business) or (m.startswith("app.ops") and not
                                                                     m.startswith("app.ops.tracing"))}
        assert not bad, f"{f.name} imports {bad}"
    for f in (APP / "ops").glob("*.py"):
        bad = {m for m in _imports(f) if m.startswith(business)}
        assert not bad, f"{f.name} imports {bad}"
    writers = [f for f in APP.rglob("*.py") if re.search(r"(insert into|update|delete from) ops_", f.read_text(
        encoding="utf-8")) and "ops" not in f.relative_to(APP).parts[:2]]
    assert writers == [], "only app/ops/tracing writes ops_ tables"
