"""App evaluation: every eval case through the real system, scored on what the app did (docs/app-eval.md).

Each case gets a fresh synthetic customer in the isolated load café (loadtest/seed.py). The customer scans the
table QR, any expected cart is pre-loaded, and then the case message goes through the signed webhook → api →
worker → router → AI (whatever LLM_BACKEND the worker runs) → guardrails → cart → reply. Scored from the DB:

- the final cart (dishes, quantities, note keyword) and sold-out dishes never in it;
- a staff request when the customer asks for a person or complains, and none when they don't;
- an allergen question answered or handed to staff;
- the AI's intent from dev_turn_traces (DEV_TRACES=true), or the router's keyword when no AI call was made;
- the customer got a reply, nothing was dead-lettered;
- per turn: inbound→reply time, the turn's own time, DB statements and DB time, LLM time (ops tracing).

    backend/.venv/Scripts/python.exe evals/app_run.py [--limit N] [--category hinglish] [--concurrency 6]

Local or staging only; synthetic data only. The worker must run with DEV_TRACES=true (local) for intents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]
for p in ("backend", "supabase/seed", "loadtest", "evals"):
    sys.path.insert(0, str(ROOT / p))

from run import load_cases  # noqa: E402
from seed import LOAD_PHONE_NUMBER_ID, TABLES  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.signature import sign  # noqa: E402
from app.domain.ai_agent import guardrails  # noqa: E402
from app.domain.conversation.router import keyword  # noqa: E402
from app.integrations.whatsapp.simulated import message_payload  # noqa: E402

WA_PREFIX = "91999700"  # + 4 digits: synthetic, never a real number
REPORTS = Path(__file__).resolve().parent / "reports"
ROUTER_INTENT = {"help": "human", "status": "order_status", "menu": "browse", "bill": "ask_question",
                 "greeting": "smalltalk", "stop": "smalltalk"}
LINE = re.compile(r"^m\d+\s+(.+?)\s*×(\d+)$")
SOLD_OUT = re.compile(r"sold out|khatam", re.I)  # the code-rendered sold-out message (domain/conversation/render)
NOT_ON_MENU = re.compile(r"not on (our|the) menu|menu mein nahi|don't have .* on (our|the) menu", re.I)


class App:
    def __init__(self, db: asyncpg.Pool, client: httpx.AsyncClient, cafe_id: str, timeout: float) -> None:
        self.db, self.client, self.cafe_id, self.timeout = db, client, cafe_id, timeout
        s = get_settings()
        self.secret = s.meta_app_secret.get_secret_value()
        self.url = s.api_url.rstrip("/") + "/webhooks/whatsapp"

    async def send(self, wa_id: str, text: str) -> tuple[str, int, float]:
        payload, wamid = message_payload(LOAD_PHONE_NUMBER_ID, wa_id, "Eval", "text", text=text)
        raw = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sign(raw, self.secret)}
        for attempt in range(4):  # like Meta: a delivery without 200 comes again
            t = time.perf_counter()
            try:
                r = await self.client.post(self.url, content=raw, headers=headers)
                if r.status_code == 200:
                    return wamid, 200, (time.perf_counter() - t) * 1000
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2**attempt)
        return wamid, 0, 0.0

    async def wait(self, wamid: str) -> bool:
        """The turn is done when the message is processed and nothing it wrote is still pending."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            try:
                row = await self.db.fetchrow(
                    """select i.processed_at is not null as processed,
                              coalesce(extract(epoch from now() - i.processed_at), 0) as since,
                              (select count(*) from wa_messages m where m.customer_id = cv.customer_id
                                 and m.created_at >= i.received_at) as outs,
                              (select count(*) from wa_messages m where m.customer_id = cv.customer_id
                                 and m.created_at >= i.received_at and m.status = 'pending') as pending,
                              (select count(*) from pending_work w where w.conversation_id = cv.id
                                 and w.status = 'dead_lettered') as dead
                       from inbound_messages i join conversations cv on cv.id = i.conversation_id
                       where i.wamid = $1""", wamid)
            except Exception:  # noqa: BLE001 (a dropped connection on a long run: poll again)
                await asyncio.sleep(2)
                continue
            if row and (row["dead"] or (row["processed"] and not row["pending"] and (row["outs"] or row["since"] > 5))):
                return True
        return False

    async def preload_cart(self, wa_id: str, lines: list[str], step: str | None) -> None:
        """Dev tooling on the synthetic load café: the cart an edit case starts from."""
        items = []
        for line in lines:
            m = LINE.match(line.strip())
            if not m:
                continue
            item_id = await self.db.fetchval("select id::text from menu_items where cafe_id = $1 and name = $2",
                                             self.cafe_id, m.group(1))
            if item_id:
                items.append({"item_id": item_id, "qty": int(m.group(2)), "note": None})
        await self.db.execute(
            """update conversations cv set state = cv.state || jsonb_build_object('cart', $3::jsonb, 'step', $4::text)
               from customers cu where cu.id = cv.customer_id and cu.cafe_id = $1 and cu.wa_id = $2""",
            self.cafe_id, wa_id, json.dumps({"items": items, "version": 1}), step or "building_cart")

    async def observe(self, wa_id: str, wamid: str) -> dict[str, Any]:
        r = await self.db.fetchrow(
            """select i.received_at, cv.id::text as conversation_id, cv.customer_id::text, cv.mode, cv.state
               from inbound_messages i join conversations cv on cv.id = i.conversation_id where i.wamid = $1""", wamid)
        if r is None:
            return {"stored": False}
        since, convo, cust = r["received_at"], r["conversation_id"], r["customer_id"]
        state = json.loads(r["state"]) if isinstance(r["state"], str) else r["state"]
        names = {x["id"]: x["name"] for x in await self.db.fetch(
            "select id::text, name from menu_items where cafe_id = $1", self.cafe_id)}
        cart: dict[str, dict[str, Any]] = {}
        for line in (state.get("cart") or {}).get("items") or []:
            name = names.get(line["item_id"], line["item_id"])
            entry = cart.setdefault(name, {"qty": 0, "notes": []})
            entry["qty"] += int(line["qty"])
            if line.get("note"):
                entry["notes"].append(line["note"])
        outs = await self.db.fetch(
            """select payload, sent_at, created_at from wa_messages where customer_id = $1 and created_at >= $2
               order by created_at, idempotency_key""", cust, since)
        replies = []
        for o in outs:
            p = json.loads(o["payload"]) if isinstance(o["payload"], str) else o["payload"]
            body = ((p.get("text") or {}).get("body")) or (((p.get("interactive") or {}).get("body") or {}).get("text")) or ""
            buttons = [b["reply"]["title"] for b in ((p.get("interactive") or {}).get("action") or {}).get("buttons") or []]
            replies.append({"text": body, "buttons": buttons})
        first_reply = min((o["sent_at"] or o["created_at"] for o in outs), default=None)
        requests = [dict(x) for x in await self.db.fetch(
            "select kind, reason_tag from requests where conversation_id = $1 and created_at >= $2", convo, since)]
        traces = await self.db.fetch(
            "select parsed, guardrails from dev_turn_traces where conversation_id = $1 and created_at >= $2 order by created_at",
            convo, since)
        parsed = [json.loads(t["parsed"]) if isinstance(t["parsed"], str) else t["parsed"] for t in traces]
        llm = [dict(x) for x in await self.db.fetch(
            "select model, outcome, latency_ms from llm_calls where conversation_id = $1 and created_at >= $2", convo, since)]
        # One pending_work row per conversation is reused turn after turn: runs are told apart by start time.
        work = await self.db.fetch("select id::text, status from pending_work where conversation_id = $1", convo)
        runs = [dict(x) for x in await self.db.fetch(
            """select name, duration_ms, queue_wait_ms, dep_ms, dep_calls, stage_ms, labels, outcome from ops_runs
               where (subject_type = 'pending_work' and subject_id = any($1::text[]) and started_at >= $3)
                  or (subject_type = 'inbound' and subject_id = $2)""", [w["id"] for w in work], wamid, since)]
        for run in runs:
            for k in ("dep_ms", "dep_calls", "stage_ms", "labels"):
                if isinstance(run[k], str):
                    run[k] = json.loads(run[k])
        return {"stored": True, "cart": cart, "mode": r["mode"], "replies": replies, "requests": requests,
                "parsed": parsed[-1] if parsed else None, "llm": llm,
                "dead_lettered": any(w["status"] == "dead_lettered" for w in work),
                "reply_ms": round((first_reply - since).total_seconds() * 1000) if first_reply else None, "runs": runs}


def score(case: dict[str, Any], seen: dict[str, Any]) -> list[str]:
    """Empty = the app did the right thing; otherwise why not."""
    if not seen.get("stored"):
        return ["message not stored"]
    exp, fails = case["expect"], []
    if seen["dead_lettered"]:
        fails.append("dead-lettered")
    if not seen["replies"]:
        fails.append("no reply to the customer")
    cart = seen["cart"]
    if "cart" in exp:
        want = {c["dish"]: c for c in exp["cart"]}
        if set(cart) != set(want):
            fails.append(f"cart {sorted(cart)} ≠ {sorted(want)}")
        for dish, c in want.items():
            if dish in cart:
                if cart[dish]["qty"] != c.get("qty", 1):
                    fails.append(f"{dish} qty {cart[dish]['qty']} ≠ {c.get('qty', 1)}")
                if c.get("note") and not any(c["note"].lower() in n.lower() for n in cart[dish]["notes"]):
                    fails.append(f"{dish} notes {cart[dish]['notes']} lack “{c['note']}”")
    for dish in exp.get("never", []):
        if dish in cart:
            fails.append(f"{dish} in the cart")
    if exp.get("never") and not any(SOLD_OUT.search(r["text"]) for r in seen["replies"]):
        fails.append("not told it's sold out" + (" (told it isn't on the menu)" if any(
            NOT_ON_MENU.search(r["text"]) for r in seen["replies"]) else ""))
    asked_staff = bool(seen["requests"]) or seen["mode"] == "human"
    if "human" in exp and asked_staff != exp["human"]:
        fails.append(f"staff request {asked_staff} ≠ {exp['human']}")
    parsed = seen["parsed"] or {}
    intents = exp.get("intent")
    if intents:
        intents = [intents] if isinstance(intents, str) else intents
        got = parsed.get("intent") if seen["parsed"] else ROUTER_INTENT.get(keyword(case["text"]) or "", None)
        if got is None and asked_staff:
            got = "human"
        if got is not None and got not in intents:
            fails.append(f"intent {got} ≠ {intents}")
    if "allergen" in exp:
        q = parsed.get("allergen_question") or {}
        norm = guardrails.normalise_allergen(str(q.get("allergen") or ""))
        if (norm[0] if norm else None) != exp["allergen"]:
            fails.append(f"allergen {q.get('allergen')} ≠ {exp['allergen']}")
        if not seen["replies"] and not any(x["kind"] == "allergy" for x in seen["requests"]):
            fails.append("allergen question neither answered nor handed to staff")
    return fails


async def fresh_customers(db: asyncpg.Pool, cafe_id: str, n: int) -> list[str]:
    used = {r["wa_id"] for r in await db.fetch("select wa_id from customers where cafe_id = $1 and wa_id like $2",
                                               cafe_id, WA_PREFIX + "%")}
    out = [w for w in (f"{WA_PREFIX}{i:04d}" for i in range(10_000)) if w not in used][:n]
    if len(out) < n:
        raise SystemExit("eval numbers used up: re-seed a fresh load café")
    # A known customer with a name, so the case message isn't taken for an answer to "what's your name?".
    await db.executemany("insert into customers (cafe_id, wa_id, name) values ($1, $2, 'Eval')",
                         [(cafe_id, w) for w in out])
    return out


async def observe_retrying(app: App, wa_id: str, wamid: str) -> dict[str, Any]:
    for attempt in range(4):
        try:
            return await app.observe(wa_id, wamid)
        except Exception:  # noqa: BLE001 (network blip: read it again)
            await asyncio.sleep(2 * (attempt + 1))
    return await app.observe(wa_id, wamid)


async def reuse(app: App, case: dict[str, Any], wa_id: str) -> dict[str, Any] | None:
    """A case an earlier, interrupted run already sent with this customer: score it from the DB."""
    wamid = await app.db.fetchval(
        """select i.wamid from inbound_messages i join conversations cv on cv.id = i.conversation_id
           join customers cu on cu.id = cv.customer_id
           where cu.cafe_id = $1 and cu.wa_id = $2 and i.payload->'text'->>'body' = $3 and i.processed_at is not null
           order by i.received_at desc limit 1""", app.cafe_id, wa_id, case["text"])
    if wamid is None:
        return None
    seen = await observe_retrying(app, wa_id, wamid)
    fails = score(case, seen)
    return {**case, "pass": not fails, "fails": fails, "ack_ms": None, "seen": seen, "reused": True}


async def one(app: App, i: int, case: dict[str, Any], wa_id: str) -> dict[str, Any]:
    qr_wamid, status, _ = await app.send(wa_id, f"Hi, I'm at Table L{i % TABLES + 1}")
    if status != 200 or not await app.wait(qr_wamid):
        return {**case, "pass": False, "fails": ["table QR turn didn't complete"], "seen": None}
    if case.get("cart_lines"):
        await app.preload_cart(wa_id, case["cart_lines"], case.get("step"))
    wamid, status, ack_ms = await app.send(wa_id, case["text"])
    if status != 200:
        return {**case, "pass": False, "fails": ["webhook not acknowledged"], "seen": None}
    done = await app.wait(wamid)
    seen = await observe_retrying(app, wa_id, wamid)
    fails = score(case, seen) if done else ["no reply within the timeout"]
    return {**case, "pass": not fails, "fails": fails, "ack_ms": round(ack_ms), "seen": seen}


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * q))], 1)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated case ids to run")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=240.0, help="seconds to wait for each turn")
    ap.add_argument("--reuse-from", type=int,
                    help="number index an interrupted run started at: cases it finished are scored from the DB")
    args = ap.parse_args()
    s = get_settings()
    if s.app_env not in ("local", "staging") or s.wa_transport != "simulator":
        raise SystemExit("app evaluation runs locally or on staging, with the simulator transport only")
    cases = load_cases(args.category)
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    cases = cases[: args.limit]
    dsn = (s.database_url_session or s.database_url).get_secret_value()
    async with asyncpg.create_pool(dsn, statement_cache_size=0, min_size=1, max_size=args.concurrency + 1) as db:
        cafe_id = await db.fetchval("select id::text from cafes where phone_number_id = $1", LOAD_PHONE_NUMBER_ID)
        if cafe_id is None:
            raise SystemExit("load café missing: run loadtest/seed.py first")
        backend = await db.fetchval("""select settings->>'llm_backend' from ops_runs where service = 'worker'
                                       order by started_at desc limit 1""")
        sem = asyncio.Semaphore(args.concurrency)
        REPORTS.mkdir(exist_ok=True)
        progress = REPORTS / f"app-{datetime.now(UTC):%Y%m%d-%H%M}.partial.jsonl"
        async with httpx.AsyncClient(timeout=60) as client:
            app = App(db, client, cafe_id, args.timeout)
            done: dict[str, dict[str, Any]] = {}
            if args.reuse_from is not None:
                for i, case in enumerate(cases):
                    res = await reuse(app, case, f"{WA_PREFIX}{args.reuse_from + i:04d}")
                    if res:
                        done[case["id"]] = res
                print(f"reused {len(done)} cases from the interrupted run", flush=True)
            todo = [c for c in cases if c["id"] not in done]
            wa_ids = await fresh_customers(db, cafe_id, len(todo))

            def show(res: dict[str, Any]) -> None:
                mark = "✓" if res["pass"] else "✗"
                print(f"{mark} {res['id']:<10} {res['category']:<14} {res['text'][:55]}"
                      + (f"\n             {'; '.join(res['fails'])}" if res["fails"] else ""), flush=True)
                with progress.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(res, ensure_ascii=False, default=str) + "\n")

            for res in done.values():
                show(res)

            async def guarded(i: int, case: dict[str, Any], wa_id: str) -> dict[str, Any]:
                async with sem:
                    try:
                        res = await one(app, i, case, wa_id)
                    except Exception as exc:  # noqa: BLE001 (one broken case must not end the run)
                        res = {**case, "pass": False, "fails": [f"harness error: {type(exc).__name__}"], "seen": None}
                    show(res)
                    return res

            started = time.perf_counter()
            ran = await asyncio.gather(*(guarded(i, c, w) for i, (c, w) in enumerate(zip(todo, wa_ids, strict=True))))
            elapsed = time.perf_counter() - started
            by_id = {**done, **{r["id"]: r for r in ran}}
            results = [by_id[c["id"]] for c in cases]

    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["pass"])
    seen = [r["seen"] for r in results if r.get("seen")]
    turns = [run for x in seen for run in x["runs"] if run["name"] == "turn"]
    hooks = [run for x in seen for run in x["runs"] if run["name"] != "turn"]
    models = defaultdict(int)
    for x in seen:
        for call in x["llm"]:
            models[f"{call['model']}:{call['outcome']}"] += 1
    summary = {
        "at": datetime.now(UTC).isoformat(), "worker_llm_backend": backend, "cases": len(results),
        "passed": sum(r["pass"] for r in results), "accuracy": round(sum(r["pass"] for r in results) / max(len(results), 1), 3),
        "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())}, "elapsed_s": round(elapsed),
        "llm_calls": dict(models),
        "reply_ms": {"p50": pct([x["reply_ms"] for x in seen if x["reply_ms"]], 0.5),
                     "p95": pct([x["reply_ms"] for x in seen if x["reply_ms"]], 0.95)},
        "turn_ms": {"p50": pct([t["duration_ms"] for t in turns if t["duration_ms"]], 0.5),
                    "p95": pct([t["duration_ms"] for t in turns if t["duration_ms"]], 0.95)},
        "turn_db_statements": {"p50": pct([(t["dep_calls"] or {}).get("db", 0) for t in turns], 0.5),
                               "max": pct([(t["dep_calls"] or {}).get("db", 0) for t in turns], 1.0)},
        "webhook_db_statements": {"p50": pct([(h["dep_calls"] or {}).get("db", 0) for h in hooks], 0.5)},
    }
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"app-{datetime.now(UTC):%Y%m%d-%H%M}.json"
    path.write_text(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False, default=str),
                    encoding="utf-8")
    print("\n" + json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"report: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
