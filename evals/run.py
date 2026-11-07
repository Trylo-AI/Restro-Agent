"""LLM eval runner (docs/rules.md#llm-evals). Deterministic scoring; the team writes and reviews the cases.

Builds the real system prompt from the seed menu (no database), sends each case through the real schema, maps
cart ops back to dish names and scores: intent · exact cart (dish, qty, note keyword) · allergen · handoff ·
recommend picks. Reports accuracy per category, p50 / p95 latency, tokens and cost per case.

`--purpose recommend` runs the Recommend-tap cases (`"purpose": "recommend"`, cases/recommend-v1.json) instead: the
RecContext comes from the case + the seed menu and allergens, code filters the candidates, and the real `recommend`
prompt and schema go to the model. Scored: picks outside ALLOWED, amounts or claims in intro / why, `any_of` (at
least one pick from the list), `never`, `min_picks`; then the recommend gate. With `--stub` the code ranking plays
the model (a wiring check that also proves every case is satisfiable without the LLM).

    backend\\.venv\\Scripts\\python.exe evals\\run.py                       # model from backend/.env
    backend\\.venv\\Scripts\\python.exe evals\\run.py --model gemini-3.5-flash-lite
    backend\\.venv\\Scripts\\python.exe evals\\run.py --stub                # wiring check, no API calls
    backend\\.venv\\Scripts\\python.exe evals\\run.py --purpose recommend --stub   # recommend gate (CI); exit 1 on fail
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
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "supabase" / "seed"))

from generate import MENU  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.repositories.menu import MenuItem  # noqa: E402
from app.domain import recommend as rec  # noqa: E402
from app.domain.ai_agent import calls, guardrails, prompt  # noqa: E402
from app.domain.ai_agent import recommend as ai_recommend  # noqa: E402
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


def seed_allergens(items: list[MenuItem]) -> dict[str, dict[str, str]]:
    """item id → allergen → state, as menu_item_allergens holds them (no row = unknown)."""
    return {it.id: dict(src.allergens) for it, src in zip(items, MENU, strict=True)}


def load_cases(only: str | None, purpose: str = "turn") -> list[dict[str, Any]]:
    """Turn cases by default (app_run.py too); `purpose: recommend` cases only for --purpose recommend."""
    cases = []
    for path in sorted(CASES.glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8")):
            if case.get("purpose", "turn") == purpose and (not only or case["category"] == only):
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
    if "recommend" in exp:
        r = data.get("recommend") if isinstance(data.get("recommend"), dict) else {}
        picks = [p for p in r.get("picks") or [] if isinstance(p, dict)]
        found = [view.by_short[str(p.get("item_ref"))] for p in picks if str(p.get("item_ref")) in view.by_short]
        if len(found) < exp["recommend"].get("min_picks", 1):
            fails.append(f"{len(found)} recommend picks < {exp['recommend'].get('min_picks', 1)}")
        for p in picks:
            if p.get("why") and guardrails.clean_why(p.get("why")) is None:
                fails.append(f"amount or claim in why “{p.get('why')}”")  # code replaces it, but it counts
    for key in ("reply", "clarify"):
        if guardrails.has_amount(data.get(key)):
            fails.append(f"amount in {key}")  # caught in production, but counts against the model
    return fails


def stub_responder(system: str, user: str) -> dict[str, Any]:
    return {"intent": "unclear", "cart_ops": [], "allergen_question": None,
            "needs_human": {"flag": False, "reason": None}, "clarify": "?", "reply": "", "recommend": None}


# ── purpose recommend ─────────────────────────────────────────────────────

def rec_context(case: dict[str, Any], view: prompt.MenuView,
                states: dict[str, dict[str, str]]) -> tuple[rec.RecContext, rec.CandidateSet, str | None]:
    """RecContext from the case (seed dish names) + the seed menu; the candidates; the prompt's MOOD phrase."""
    by_name = {it.name: it for it in view.items}
    states = {i: dict(v) for i, v in states.items()}
    for dish, override in (case.get("allergens") or {}).items():  # a café that marked more dishes
        states.setdefault(by_name[dish].id, {}).update(override)
    hh, mm = (int(x) for x in case.get("time", "15:40").split(":"))
    rc = rec.RecContext(
        items=list(view.items), allergen_states=states,
        prefs=[(k, v) for k, v in case.get("prefs", [])] + [("allergy", a) for a in case.get("allergies", [])],
        visit_allergies=list(case.get("visit_allergies", [])),
        history=[rec.HistoryItem(by_name[d].id, t, t, ago) for d, t, ago in case.get("history", [])],
        popular=[by_name[d].id for d in case.get("popular", [])], cart_ids=[by_name[d].id for d in case.get("cart", [])],
        local_time=datetime(2026, 11, 6, hh, mm, tzinfo=ZoneInfo("Asia/Kolkata")),
        near_closing=bool(case.get("near_closing")), first_name=case.get("first_name", "Test"),
        language=case.get("language", "English"), view=view)
    cs = rec.filter_candidates(rc, exclude=frozenset(by_name[d].id for d in case.get("shown", [])))
    mood = case.get("mood")
    phrase = rec.MOODS[mood][1] if mood in rec.MOODS else rec.sanitise_mood(case.get("mood_text"))
    return rc, cs, phrase


def code_responder(rc: rec.RecContext, cs: rec.CandidateSet, mood: str | None) -> Any:
    """--stub: the code ranking plays the model, so every case is checked against what code alone would pick."""
    assert rc.view is not None
    view = rc.view
    picks = rec.fallback_rank(rc, cs, mood=mood)

    def respond(system: str, user: str) -> dict[str, Any]:  # code `why` may hold a count; model text never does
        return {"intro": "A few ideas for you",
                "picks": [{"item_ref": view.short_of[p.item.id], "why": guardrails.clean_why(p.why) or "A good fit right now"}
                          for p in picks]}
    return respond


def score_recommend(case: dict[str, Any], data: dict[str, Any] | None, view: prompt.MenuView,
                    cs: rec.CandidateSet) -> tuple[list[str], dict[str, Any]]:
    """Fails, plus the gate counts: picks outside ALLOWED, model text with an amount or claim, the any_of hit."""
    gate: dict[str, Any] = {"outside_allowed": 0, "unsafe_text": 0, "any_of": None}
    if data is None:
        return ["no valid output"], gate
    exp = case["expect"]
    fails = []
    for p in [p for p in data.get("picks") or [] if isinstance(p, dict)]:
        item = view.by_short.get(str(p.get("item_ref")))
        if item is None or item.id not in cs.ids:
            gate["outside_allowed"] += 1
            fails.append(f"pick {p.get('item_ref')} ({item.name if item else 'unknown'}) outside ALLOWED")
        if p.get("why") and guardrails.clean_why(p.get("why")) is None:
            gate["unsafe_text"] += 1
            fails.append(f"amount or claim in why “{p.get('why')}”")
    if data.get("intro") and guardrails.clean_why(data.get("intro"), ai_recommend.INTRO_MAX) is None:
        gate["unsafe_text"] += 1
        fails.append(f"amount or claim in intro “{data.get('intro')}”")
    names = [item.name for item, _ in ai_recommend.validate(data, view, cs.ids).picks]
    if len(names) < exp.get("min_picks", 1):
        fails.append(f"{len(names)} valid picks < {exp.get('min_picks', 1)}")
    if exp.get("any_of"):
        gate["any_of"] = bool(set(names) & set(exp["any_of"]))
        if not gate["any_of"]:
            fails.append(f"picks {names} none of {exp['any_of']}")
    bad = sorted(set(names) & set(exp.get("never", [])))
    if bad:
        fails.append(f"picked {bad}")
    return fails, gate


def recommend_gate(results: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    """docs/rules.md recommend gate: 0 picks outside ALLOWED · 0 amounts or claims reaching the customer ·
    any_of ≥ 90 % · p95 ≤ 3 s."""
    scored = [r for r in results if r["pass"] is not None]
    any_of = [r["gate"]["any_of"] for r in scored if r["gate"]["any_of"] is not None]
    lat = sorted(r["latency_ms"] for r in scored) or [0]
    m = {"outside_allowed": sum(r["gate"]["outside_allowed"] for r in scored),
         "unsafe_text": sum(r["gate"]["unsafe_text"] for r in scored),
         "any_of_pct": round(100 * sum(any_of) / max(len(any_of), 1), 1),
         "no_output": sum(1 for r in scored if r["outcome"] != "ok"),
         "p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))]}
    ok = m["outside_allowed"] == 0 and m["unsafe_text"] == 0 and m["any_of_pct"] >= 90 and m["p95_ms"] <= 3000
    return ok, m


async def run_recommend(args: argparse.Namespace, model: str, view: prompt.MenuView,
                        states: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    s = get_settings()
    out_schema = calls.schema("recommend")
    results = []
    for case in load_cases(args.category, "recommend")[: args.limit]:
        rc, cs, phrase = rec_context(case, view, states)
        if args.stub:
            llm.set_client(StubLlm(code_responder(rc, cs, case.get("mood") or rec.guess_mood(case.get("mood_text")))))
        shown = [it.id for it in view.items if it.name in case.get("shown", [])]
        system, text = ai_recommend.build_system(view, rc.language), ai_recommend.suffix(rc, cs, mood=phrase, shown=shown)
        for attempt in range(4):
            r = await llm.client().generate(model=model, system=system, prompt=text, schema=out_schema,
                                            timeout_s=s.llm_recommend_timeout_seconds,
                                            max_output_tokens=ai_recommend.MAX_OUTPUT_TOKENS)
            if r.outcome != "rate_limited" or args.stub:
                break
            await asyncio.sleep(20 * (attempt + 1))
        if r.outcome in ("rate_limited", "quota_exhausted"):
            results.append({"id": case["id"], "category": case["category"], "text": case.get("note", ""), "pass": None,
                            "fails": [f"skipped: {r.outcome}"], "outcome": r.outcome, "latency_ms": r.latency_ms,
                            "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "output": None, "answered_by": None,
                            "gate": None})
            print(f"- {case['id']:<8} {case['category']:<14} skipped ({r.outcome})")
            continue
        fails, gate = score_recommend(case, r.data if r.ok else None, view, cs)
        results.append({"id": case["id"], "category": case["category"], "text": case.get("note", ""), "pass": not fails,
                        "fails": fails, "outcome": r.outcome, "latency_ms": r.latency_ms, "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens, "answered_by": r.model, "candidates": len(cs.items),
                        "cost": 0.0 if r.provider == "dashscope" else pricing.cost(model, r.input_tokens,
                                                                                    r.cached_tokens, r.output_tokens),
                        "output": r.data, "raw": None if r.data else r.raw[:500], "gate": gate})
        print(f"{'✓' if not fails else '✗'} {case['id']:<8} {case['category']:<14} {r.latency_ms:>5} ms  "
              f"{len(cs.items):>2} candidates  {case.get('note', '')[:50]}"
              + (f"\n         {'; '.join(fails)}" if fails else ""))
        if not args.stub:
            await asyncio.sleep(args.delay)
    return results


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
    ap.add_argument("--purpose", choices=["turn", "recommend"], default="turn",
                    help="recommend = the Recommend-tap cases (cases/recommend-v1.json) and the recommend gate")
    args = ap.parse_args()
    s = get_settings()
    model = args.model or ((s.llm_recommend_model or s.llm_model_primary) if args.purpose == "recommend"
                           else s.llm_model_primary)
    dash = None
    if args.stub:
        llm.set_client(StubLlm(stub_responder))
    elif args.provider == "dashscope":
        from dashscope import DashScopeClient

        dash = DashScopeClient(chain=[m for m in args.chain.split(",") if m])
        llm.set_client(dash)
    everything, available = seed_menu()
    view = prompt.build_view("Kaapi House", "warm", available)
    if args.purpose == "recommend":
        finish(args, model, dash, await run_recommend(args, model, view, seed_allergens(everything)))
        return
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
                             editing=None, open_orders=[], chat=[(who, msg) for who, msg in case.get("chat", [])],
                             text=case["text"])
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
    finish(args, model, dash, results)


def finish(args: argparse.Namespace, model: str, dash: Any, results: list[dict[str, Any]]) -> None:
    scored = [r for r in results if r["pass"] is not None]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for res in scored:
        by_cat[res["category"]].append(res["pass"])
    lat = sorted(r["latency_ms"] for r in scored) or [0]
    summary = {
        "model": model, "provider": llm.client().provider, "purpose": args.purpose, "at": datetime.now(UTC).isoformat(),
        "cases": len(results),
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
    gate_ok = True
    if args.purpose == "recommend":
        gate_ok, summary["recommend_gate"] = recommend_gate(results)
        summary["recommend_gate"]["pass"] = gate_ok
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    if not args.stub:
        REPORTS.mkdir(exist_ok=True)
        name = f"{datetime.now(UTC):%Y%m%d-%H%M}-{'rec-' if args.purpose == 'recommend' else ''}{model}.json"
        (REPORTS / name).write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
        print(f"report: evals/reports/{name}")
    if not gate_ok:
        sys.exit(1)  # CI runs the recommend gate with --stub


if __name__ == "__main__":
    asyncio.run(main())
