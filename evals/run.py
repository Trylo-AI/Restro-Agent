"""LLM eval runner (docs/rules.md#llm-evals). Deterministic scoring; the team writes and reviews the cases.

Builds the real system prompt from the seed menu (no database), sends each case through the real schema, maps
cart ops back to dish names and scores: intent · exact cart (dish, qty, note keyword) · allergen · handoff.
Reports accuracy per category, p50 / p95 latency, tokens and cost per case.

    backend\\.venv\\Scripts\\python.exe evals\\run.py                       # model from backend/.env
    backend\\.venv\\Scripts\\python.exe evals\\run.py --model gemini-3.5-flash-lite
    backend\\.venv\\Scripts\\python.exe evals\\run.py --stub                # wiring check, no API calls
    backend\\.venv\\Scripts\\python.exe evals\\run.py --provider dashscope --model qwen3.8-flash  # Alibaba free tier

Synthetic data only: cases never contain real names or numbers.
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "supabase" / "seed"))

from generate import MENU  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.repositories.menu import MenuItem  # noqa: E402
from app.domain.ai_agent import calls, guardrails, prompt  # noqa: E402
from app.integrations import llm  # noqa: E402
from app.integrations.llm import StubLlm, pricing  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
REPORTS = Path(__file__).resolve().parent / "reports"


def seed_menu() -> tuple[list[MenuItem], list[MenuItem]]:
    items = [MenuItem(id=f"seed-{i}", name=it.name, description=it.description or None, category=it.category,
                      price_paise=it.rupees * 100, prep_minutes=it.prep, veg=it.veg, spice=it.spice,
                      available=it.available, version=1) for i, it in enumerate(MENU)]
    available = sorted([i for i in items if i.available], key=lambda i: (i.category, i.name))
    return items, available


def load_cases(only: str | None) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(CASES.glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8")):
            if not only or case["category"] == only:
                cases.append(case)
    return cases


def score(case: dict[str, Any], data: dict[str, Any] | None, view: prompt.MenuView, everything: list[MenuItem]) -> list[str]:
    """Empty list = pass; otherwise the reasons it failed."""
    if data is None:
        return ["no valid output"]
    exp = case["expect"]
    fails = []
    intents = exp.get("intent")
    if intents and data.get("intent") not in ([intents] if isinstance(intents, str) else intents):
        fails.append(f"intent {data.get('intent')} ≠ {intents}")
    if "cart" in exp:
        got: dict[str, tuple[int, str]] = {}
        for op in data.get("cart_ops") or []:
            if op.get("op") != "add":
                continue
            item = view.by_short.get(str(op.get("item_ref"))) or guardrails.match_by_name(str(op.get("item_ref")), view.items)
            if item:
                qty, note = got.get(item.name, (0, ""))
                got[item.name] = (qty + int(op.get("qty") or 1), f"{note} {op.get('note') or ''}".strip())
        want = {c["dish"]: c for c in exp["cart"]}
        if set(got) != set(want):
            fails.append(f"cart dishes {sorted(got)} ≠ {sorted(want)}")
        for dish, c in want.items():
            if dish in got:
                if got[dish][0] != c.get("qty", 1):
                    fails.append(f"{dish} qty {got[dish][0]} ≠ {c.get('qty', 1)}")
                if c.get("note") and c["note"].lower() not in got[dish][1].lower():
                    fails.append(f"{dish} note “{got[dish][1]}” lacks “{c['note']}”")
    if "never" in exp:  # e.g. a sold-out dish must not be added under its id
        for op in data.get("cart_ops") or []:
            item = view.by_short.get(str(op.get("item_ref")))
            if item and item.name in exp["never"]:
                fails.append(f"added {item.name}")
    if "allergen" in exp:
        q = data.get("allergen_question") or {}
        norm = guardrails.normalise_allergen(str(q.get("allergen") or ""))
        if (norm[0] if norm else None) != exp["allergen"]:
            fails.append(f"allergen {q.get('allergen')} ≠ {exp['allergen']}")
    if "human" in exp:
        human = data.get("intent") == "human" or bool((data.get("needs_human") or {}).get("flag"))
        if human != exp["human"]:
            fails.append(f"human {human} ≠ {exp['human']}")
    for key in ("reply", "clarify"):
        if guardrails.has_amount(data.get(key)):
            fails.append(f"amount in {key}")  # caught in production, but counts against the model
    return fails


def stub_responder(system: str, user: str) -> dict[str, Any]:
    return {"intent": "unclear", "cart_ops": [], "allergen_question": None,
            "needs_human": {"flag": False, "reason": None}, "clarify": "?", "reply": ""}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--category")
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between calls (free-tier rate limits)")
    ap.add_argument("--provider", choices=["app", "dashscope"], default="app",
                    help="app = LLM_BACKEND from backend/.env; dashscope = Alibaba Model Studio free tier (evals only)")
    ap.add_argument("--chain", default="", help="dashscope: comma-separated models to use when --model's quota is gone")
    ap.add_argument("--token-cap", type=int, default=600_000,
                    help="dashscope: stop before a model passes this many tokens (free quota is 1M)")
    ap.add_argument("--limit", type=int, help="only the first N cases (a quick check)")
    args = ap.parse_args()
    s = get_settings()
    model = args.model or s.llm_model_primary
    dash = None
    if args.stub:
        llm.set_client(StubLlm(stub_responder))
    elif args.provider == "dashscope":
        from dashscope import DashScopeClient

        dash = DashScopeClient(chain=[m for m in args.chain.split(",") if m])
        llm.set_client(dash)
    everything, available = seed_menu()
    view = prompt.build_view("Kaapi House", "warm", available)
    out_schema = calls.schema("turn")
    cases = load_cases(args.category)[: args.limit]
    results = []
    for case in cases:
        if dash and sum(dash.tokens_used.values()) >= args.token_cap:
            results.append({"id": case["id"], "category": case["category"], "text": case["text"], "pass": None,
                            "fails": ["skipped: token cap"], "outcome": "token_cap", "latency_ms": 0,
                            "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "output": None, "answered_by": None})
            continue
        text = prompt.suffix(summary={"first_name": "Test", "visits": case.get("visits", 0),
                                      "preferences": case.get("preferences", {})},
                             hours={"open": True}, step=case.get("step", "browsing"), cart_lines=case.get("cart_lines", []),
                             editing=None, open_orders=[], chat=[], text=case["text"])
        for attempt in range(4):  # free-tier quota: back off instead of scoring a refusal as a wrong answer
            r = await llm.client().generate(model=model, system=view.system, prompt=text, schema=out_schema,
                                            timeout_s=s.llm_timeout_seconds, max_output_tokens=600)
            if r.outcome != "rate_limited" or args.stub:
                break
            await asyncio.sleep(20 * (attempt + 1))
        if r.outcome in ("rate_limited", "quota_exhausted"):
            results.append({"id": case["id"], "category": case["category"], "text": case["text"], "pass": None,
                            "fails": [f"skipped: {r.outcome}"], "outcome": r.outcome, "latency_ms": r.latency_ms,
                            "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "output": None, "answered_by": None})
            print(f"- {case['id']:<6} {case['category']:<14} skipped ({r.outcome})")
            continue
        fails = score(case, r.data if r.ok else None, view, everything)
        results.append({"id": case["id"], "category": case["category"], "text": case["text"], "pass": not fails,
                        "fails": fails, "outcome": r.outcome, "latency_ms": r.latency_ms, "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens, "answered_by": r.model,
                        "cost": 0.0 if dash else pricing.cost(model, r.input_tokens, r.cached_tokens, r.output_tokens),
                        "output": r.data, "raw": None if r.data else r.raw[:500]})
        mark = "✓" if not fails else "✗"
        print(f"{mark} {case['id']:<6} {case['category']:<14} {r.latency_ms:>5} ms  {case['text'][:60]}"
              + (f"\n         {'; '.join(fails)}" if fails else ""))
        if not args.stub:
            await asyncio.sleep(args.delay)

    scored = [r for r in results if r["pass"] is not None]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for res in scored:
        by_cat[res["category"]].append(res["pass"])
    lat = sorted(r["latency_ms"] for r in scored) or [0]
    summary = {
        "model": model, "provider": llm.client().provider, "at": datetime.now(UTC).isoformat(), "cases": len(results),
        "scored": len(scored), "skipped": len(results) - len(scored),
        "accuracy": round(sum(r["pass"] for r in scored) / max(len(scored), 1), 3),
        "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())},
        "latency_p50_ms": statistics.median(lat), "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
        "avg_input_tokens": round(statistics.mean(r["input_tokens"] for r in scored), 1) if scored else 0,
        "avg_output_tokens": round(statistics.mean(r["output_tokens"] for r in scored), 1) if scored else 0,
        "cost_per_case_usd": round(sum(r["cost"] for r in scored) / max(len(scored), 1), 6),
        "pricing_version": "dashscope-free-tier" if dash else pricing.PRICING_VERSION,
    }
    if dash:
        summary.update({
            "tokens_used": dash.tokens_used, "json_mode": dash.schema_mode, "exhausted": sorted(dash.exhausted),
            "dropped_params": {m: sorted(v) for m, v in dash.dropped.items()}, "errors": dash.last_error,
            "answered_by": {m: sum(1 for x in scored if x["answered_by"] == m)
                            for m in sorted({x["answered_by"] for x in scored})},
        })
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    if not args.stub:
        REPORTS.mkdir(exist_ok=True)
        name = f"{datetime.now(UTC):%Y%m%d-%H%M}-{model}.json"
        (REPORTS / name).write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
        print(f"report: evals/reports/{name}")


if __name__ == "__main__":
    asyncio.run(main())
