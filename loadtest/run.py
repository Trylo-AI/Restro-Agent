"""Load test (docs/rules.md#load-tests): N synthetic customers order through the real, signed webhook at the same
time, then the database is checked for lost or duplicate messages, dead letters and inbound-to-reply latency.

Local or staging only, never prod or a real number. Seed the load café first (loadtest/seed.py), and run the worker
with LLM_BACKEND=stub_menu for most runs (one short real-model run checks quotas).

    backend/.venv/Scripts/python.exe loadtest/run.py --scenario smoke
    backend/.venv/Scripts/python.exe loadtest/run.py --scenario peak --redeliver 0.1

Each customer: table QR → name (if asked) → an order → "that's all" → taps Confirm. Replies are read from the DB by
one poller, so the harness adds a single connection however many customers run. Exit code 1 when a check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed import LOAD_PHONE_NUMBER_ID, TABLES  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.signature import sign  # noqa: E402
from app.integrations.whatsapp.simulated import message_payload  # noqa: E402

WA_PREFIX = "91999800"  # + 4 digits: synthetic, 12 digits, never a real number
NAMES = ["Asha", "Vikram", "Meera", "Arjun", "Zoya", "Kabir", "Isha", "Rohan", "Tara", "Dev", "Nisha", "Farhan"]
ORDERS = ["2 Cold Coffee and 1 Chocolate Brownie", "1 Cappuccino", "1 Masala Chai and 1 Poha", "2 Flat White",
          "1 Bombay Sandwich and 1 Lemon Iced Tea", "1 Mushroom Risotto", "3 Masala Chai",
          "1 Avocado Toast and 1 Café Latte"]

SCENARIOS: dict[str, dict[str, Any]] = {
    "smoke": {"conversations": 5, "spread_s": 5, "think": (1.0, 2.0)},
    "steady": {"conversations": 20, "spread_s": 60, "think": (3.0, 8.0)},
    "peak": {"conversations": 60, "spread_s": 30, "think": (2.0, 5.0)},
    "burst": {"conversations": 30, "spread_s": 2, "think": (0.3, 1.0)},
    "gate": {"conversations": 100, "spread_s": 20, "think": (2.0, 6.0)},  # the Phase 3 gate: staging
}
P95_LIMIT_MS = 5000
RTT_GATE_MS = 20  # same-region DB round trips are ~1–2 ms
NAME_ASK = re.compile(r"your name|naam", re.I)
MAX_STEPS = 8


# ── pure parts (unit-tested in backend/tests/unit/test_loadtest.py) ──────────────────────────


def bodies(outs: list[dict[str, Any]]) -> str:
    """All customer-visible text of the replies, lower-cased."""
    parts = []
    for o in outs:
        p = o.get("payload") or {}
        parts.append(((p.get("text") or {}).get("body")) or "")
        inter = p.get("interactive") or {}
        parts.append(((inter.get("body") or {}).get("text")) or "")
    return "\n".join(parts).lower()


def button(outs: list[dict[str, Any]], prefix: str) -> tuple[str, str] | None:
    """(id, title) of the last reply button whose id starts with prefix."""
    found = None
    for o in outs:
        inter = (o.get("payload") or {}).get("interactive") or {}
        for b in (inter.get("action") or {}).get("buttons") or []:
            reply = b.get("reply") or {}
            if str(reply.get("id", "")).startswith(prefix):
                found = (reply["id"], reply.get("title", ""))
    return found


@dataclass
class Flow:
    named: bool = False
    ordered: bool = False
    done: bool = False
    confirmed: bool = False


def next_action(outs: list[dict[str, Any]], flow: Flow, name: str, order: str) -> tuple[str, dict[str, Any]] | None:
    """What the customer does after this reply; None when the conversation is over."""
    if flow.confirmed:
        return None
    tap = button(outs, "confirm:")
    if tap:
        flow.confirmed = True
        return "button", {"reply_id": tap[0], "reply_title": tap[1]}
    if not flow.named and NAME_ASK.search(bodies(outs)):
        flow.named = True
        return "text", {"text": name}
    if not flow.ordered:
        flow.ordered = True
        return "text", {"text": order}
    if not flow.done:
        flow.done = True
        return "text", {"text": "that's all"}
    return None


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def verdict(r: dict[str, Any]) -> list[str]:
    """Failed checks, empty when the run passes (rules.md: zero lost or duplicate, p95 ≤ 5 s, no errors)."""
    fails = []
    if r["lost_inbound"]:
        fails.append(f"{r['lost_inbound']} acknowledged messages never stored")
    if r["unprocessed"]:
        fails.append(f"{r['unprocessed']} messages never processed")
    if r["duplicate_outbound"]:
        fails.append(f"{r['duplicate_outbound']} duplicate replies")
    if r["duplicate_orders"]:
        fails.append(f"{r['duplicate_orders']} customers with more than one order")
    if r["dead_lettered"]:
        fails.append(f"{r['dead_lettered']} turns dead-lettered")
    if r["outbound_not_sent"]:
        fails.append(f"{r['outbound_not_sent']} replies not sent (pending, failed or ambiguous)")
    if r["webhook_errors"]:
        fails.append(f"{r['webhook_errors']} webhook calls not acknowledged with 200")
    if r["stuck_conversations"]:
        fails.append(f"{r['stuck_conversations']} customers got no reply in time")
    p95 = r["reply_ms"]["p95"]
    if p95 is None:
        fails.append("no inbound-to-reply time measured")
    elif p95 > P95_LIMIT_MS and r.get("latency_gate", True):
        fails.append(f"inbound-to-reply p95 {p95} ms > {P95_LIMIT_MS} ms")
    return fails


def latency_note(r: dict[str, Any]) -> str | None:
    """From a laptop every statement crosses the internet to the DB (a turn makes dozens), so the p95 gate only
    applies where the DB is near: staging in the same region."""
    if r.get("latency_gate", True):
        return None
    return (f"latency not gated: DB round trip {r['db_rtt_ms']} ms from here (> {RTT_GATE_MS} ms). "
            f"Inbound-to-reply p95 {r['reply_ms']['p95']} ms is dominated by it; run the gate on staging.")


# ── the run ────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Stats:
    acked: set[str] = field(default_factory=set)
    redelivered: int = 0
    ack_ms: list[float] = field(default_factory=list)
    webhook_errors: dict[str, int] = field(default_factory=dict)
    stuck: int = 0
    gave_up: int = 0
    confirmed: int = 0


class Harness:
    def __init__(self, args: argparse.Namespace, client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
        self.args = args
        self.client = client
        self.db = db
        self.secret = get_settings().meta_app_secret.get_secret_value()
        self.url = args.target.rstrip("/") + "/webhooks/whatsapp"
        self.rng = random.Random(args.seed)  # noqa: S311 (timing jitter, not secrets)
        self.stats = Stats()
        self.waiting: dict[str, asyncio.Future[list[dict[str, Any]] | None]] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    async def _post(self, raw: bytes) -> int:
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sign(raw, self.secret)}
        started = time.perf_counter()
        try:
            r = await self.client.post(self.url, content=raw, headers=headers)
            status = r.status_code
        except httpx.HTTPError as e:
            status = 0
            self.stats.webhook_errors[type(e).__name__] = self.stats.webhook_errors.get(type(e).__name__, 0) + 1
        self.stats.ack_ms.append((time.perf_counter() - started) * 1000)
        if status and status != 200:
            self.stats.webhook_errors[str(status)] = self.stats.webhook_errors.get(str(status), 0) + 1
        return status

    async def _redeliver(self, raw: bytes) -> None:
        await asyncio.sleep(self.rng.uniform(0.5, 3.0))  # Meta delivers at least once: the same body again
        self.stats.redelivered += 1
        await self._post(raw)

    async def say(self, wa_id: str, name: str, kind: str, **kw: Any) -> list[dict[str, Any]] | None:
        payload, wamid = message_payload(LOAD_PHONE_NUMBER_ID, wa_id, name,
                                         "interactive" if kind == "button" else kind, **kw)
        raw = json.dumps(payload, separators=(",", ":")).encode()
        fut: asyncio.Future[list[dict[str, Any]] | None] = asyncio.get_running_loop().create_future()
        self.waiting[wamid] = fut
        for attempt in range(4):  # like Meta: a delivery without 200 comes again, with backoff
            if await self._post(raw) == 200:
                break
            await asyncio.sleep(2**attempt)
        else:
            self.stats.gave_up += 1
            self.waiting.pop(wamid, None)
            return None
        self.stats.acked.add(wamid)
        if self.rng.random() < self.args.redeliver:
            t = asyncio.create_task(self._redeliver(raw))
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)
        try:
            return await asyncio.wait_for(fut, self.args.reply_timeout)
        except TimeoutError:
            return None
        finally:
            self.waiting.pop(wamid, None)

    async def customer(self, n: int, wa_id: str, start_at: float, think: tuple[float, float]) -> None:
        await asyncio.sleep(start_at)
        name, order = NAMES[n % len(NAMES)], ORDERS[n % len(ORDERS)]
        flow = Flow()
        outs = await self.say(wa_id, name, "text", text=f"Hi, I'm at Table L{n % TABLES + 1}")
        for _ in range(MAX_STEPS):
            if outs is None:
                self.stats.stuck += 1
                return
            action = next_action(outs, flow, name, order)
            if action is None:
                break
            await asyncio.sleep(self.rng.uniform(*think))
            kind, kw = action
            outs = await self.say(wa_id, name, kind, **kw)
        if flow.confirmed and outs is not None:
            self.stats.confirmed += 1

    async def poll(self, stop: asyncio.Event) -> None:
        """One query every 300 ms for every message still waiting: replies are ready when the turn is processed
        and nothing it wrote is still pending (or it was processed 5 s ago with no reply at all)."""
        while not stop.is_set():
            await asyncio.sleep(0.3)
            wamids = [w for w, f in self.waiting.items() if not f.done()]
            if not wamids:
                continue
            try:
                rows = await self.db.fetch(
                    """select i.wamid, i.processed_at is not null as processed,
                              coalesce(extract(epoch from now() - i.processed_at), 0) as since,
                              coalesce((select json_agg(json_build_object('payload', m.payload, 'status', m.status)
                                                        order by m.created_at, m.idempotency_key)
                                        from wa_messages m where m.cafe_id = i.cafe_id and m.customer_id = cv.customer_id
                                          and m.created_at >= i.received_at), '[]') as outs
                       from inbound_messages i join conversations cv on cv.id = i.conversation_id
                       where i.wamid = any($1::text[])""", wamids)
            except (asyncpg.PostgresError, OSError):
                continue  # a network blip on the harness side: try again next tick
            for r in rows:
                outs = json.loads(r["outs"])
                if not r["processed"]:
                    continue
                if (outs and all(o["status"] != "pending" for o in outs)) or (not outs and r["since"] > 5):
                    fut = self.waiting.get(r["wamid"])
                    if fut and not fut.done():
                        fut.set_result(outs)


async def collect(db: asyncpg.Connection, cafe_id: str, wa_ids: list[str], since: datetime,
                  acked: set[str]) -> dict[str, Any]:
    cust = "select id from customers where cafe_id = $1 and wa_id = any($2::text[])"
    found = {r["wamid"] for r in await db.fetch(
        f"""select i.wamid from inbound_messages i join conversations cv on cv.id = i.conversation_id
            where cv.customer_id in ({cust}) and i.received_at >= $3""", cafe_id, wa_ids, since)}  # noqa: S608
    row = await db.fetchrow(
        f"""select
              (select count(*) from inbound_messages i join conversations cv on cv.id = i.conversation_id
                where cv.customer_id in ({cust}) and i.received_at >= $3 and i.processed_at is null) as unprocessed,
              (select count(*) from pending_work w join conversations cv on cv.id = w.conversation_id
                where cv.customer_id in ({cust}) and w.created_at >= $3 and w.status = 'dead_lettered') as dead_lettered,
              (select count(*) from pending_work w join conversations cv on cv.id = w.conversation_id
                where cv.customer_id in ({cust}) and w.created_at >= $3) as turns,
              (select count(*) from wa_messages where customer_id in ({cust}) and created_at >= $3) as outbound,
              (select count(*) from wa_messages where customer_id in ({cust}) and created_at >= $3
                and status in ('pending', 'failed', 'ambiguous')) as outbound_not_sent,
              (select count(*) from (
                 select created_at - lag(created_at) over (partition by customer_id, payload::text order by created_at) as gap
                 from wa_messages where customer_id in ({cust}) and created_at >= $3) d
                where d.gap < interval '3 seconds') as duplicate_outbound,
              (select count(*) from orders where customer_id in ({cust}) and created_at >= $3) as orders,
              (select count(*) from (select customer_id from orders where customer_id in ({cust}) and created_at >= $3
                                     group by customer_id having count(*) > 1) d) as duplicate_orders,
              (select count(*) from llm_calls l join conversations cv on cv.id = l.conversation_id
                where cv.customer_id in ({cust}) and l.created_at >= $3) as llm_calls""",  # noqa: S608 ({cust} is a constant; values are bind parameters)
        cafe_id, wa_ids, since)
    lat = [float(r["ms"]) for r in await db.fetch(
        f"""select extract(epoch from (select min(coalesce(m.sent_at, m.created_at)) from wa_messages m
                                       where m.customer_id = cv.customer_id and m.created_at >= i.received_at)
                                - i.received_at) * 1000 as ms
            from inbound_messages i join conversations cv on cv.id = i.conversation_id
            where cv.customer_id in ({cust}) and i.received_at >= $3""", cafe_id, wa_ids, since)  # noqa: S608
           if r["ms"] is not None]
    return {**dict(row), "inbound_stored": len(found), "lost_inbound": len(acked - found),
            "reply_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": pct(lat, 1.0), "n": len(lat)}}


async def fresh_numbers(db: asyncpg.Connection, cafe_id: str, n: int) -> list[str]:
    """New synthetic customers every run, so each one starts with the new-customer flow."""
    used = {r["wa_id"] for r in await db.fetch("select wa_id from customers where cafe_id = $1 and wa_id like $2",
                                               cafe_id, WA_PREFIX + "%")}
    out = [f"{WA_PREFIX}{i:04d}" for i in range(10_000) if f"{WA_PREFIX}{i:04d}" not in used][:n]
    if len(out) < n:
        raise SystemExit("load café out of synthetic numbers: re-seed a fresh load café")
    return out


def save(report: dict[str, Any]) -> Path:
    out = Path(__file__).resolve().parent / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{report['scenario']}.json"
    path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    return path


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=SCENARIOS, default="smoke")
    ap.add_argument("--conversations", type=int, help="override the scenario's customer count")
    ap.add_argument("--target", default=None, help="api base URL (default API_URL from backend/.env)")
    ap.add_argument("--redeliver", type=float, default=0.1, help="share of webhooks delivered twice")
    ap.add_argument("--reply-timeout", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--real-llm", action="store_true", help="allow a worker on a real model (spends quota)")
    args = ap.parse_args()

    s = get_settings()
    args.target = args.target or s.api_url
    host = urlparse(args.target).hostname or ""
    if s.app_env == "prod" or (host not in ("localhost", "127.0.0.1") and s.app_env != "staging"):
        raise SystemExit("load tests run against local or staging only")
    if s.wa_transport != "simulator":
        raise SystemExit("load tests never send through Meta: WA_TRANSPORT must be simulator")
    sc = SCENARIOS[args.scenario]
    n = args.conversations or sc["conversations"]

    db = await asyncpg.connect((s.database_url_session or s.database_url).get_secret_value(), statement_cache_size=0)
    try:
        cafe_id = await db.fetchval("select id::text from cafes where phone_number_id = $1", LOAD_PHONE_NUMBER_ID)
        if cafe_id is None:
            raise SystemExit("load café missing: run loadtest/seed.py first")
        backend = await db.fetchval("""select settings->>'llm_backend' from ops_runs where service = 'worker'
                                       order by started_at desc limit 1""")
        if backend not in (None, "stub", "stub_menu") and not args.real_llm:
            raise SystemExit(f"the worker last ran with LLM_BACKEND={backend}: restart it with stub_menu "
                             "or pass --real-llm (spends model quota)")
        wa_ids = await fresh_numbers(db, cafe_id, n)
        rtts = []
        for _ in range(5):
            t = time.perf_counter()
            await db.fetchval("select 1")
            rtts.append((time.perf_counter() - t) * 1000)
        db_rtt_ms = pct(rtts, 0.5)
        since = await db.fetchval("select now()")
        stop = asyncio.Event()
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        async with httpx.AsyncClient(timeout=30, limits=limits) as client:
            h = Harness(args, client, db)
            poller = asyncio.create_task(h.poll(stop))
            started = time.perf_counter()
            print(f"{args.scenario}: {n} customers over {sc['spread_s']} s, think {sc['think']} s, "
                  f"redeliver {args.redeliver:.0%} → {args.target}")
            await asyncio.gather(*(h.customer(i, wa, h.rng.uniform(0, sc["spread_s"]), sc["think"])
                                   for i, wa in enumerate(wa_ids)))
            await asyncio.gather(*h.tasks)
            elapsed = time.perf_counter() - started
            stop.set()
            await poller
        await asyncio.sleep(3)  # let the last status updates land
        result = await collect(db, cafe_id, wa_ids, since, h.stats.acked)
    finally:
        await db.close()

    report = {
        "scenario": args.scenario, "customers": n, "elapsed_s": round(elapsed, 1), "target": args.target,
        "webhooks_acked": len(h.stats.acked), "redelivered": h.stats.redelivered,
        "webhook_ack_ms": {"p50": pct(h.stats.ack_ms, 0.5), "p95": pct(h.stats.ack_ms, 0.95), "max": pct(h.stats.ack_ms, 1.0)},
        "webhook_errors": sum(h.stats.webhook_errors.values()), "webhook_error_kinds": h.stats.webhook_errors,
        "stuck_conversations": h.stats.stuck, "webhook_gave_up": h.stats.gave_up, "confirmed": h.stats.confirmed,
        **result,
        "db_rtt_ms": db_rtt_ms, "latency_gate": db_rtt_ms is not None and db_rtt_ms <= RTT_GATE_MS,
    }
    report["llm_calls_per_order"] = round(report["llm_calls"] / report["orders"], 2) if report["orders"] else None
    fails = verdict(report)
    report["fails"] = fails
    path = await asyncio.to_thread(save, report)
    print(json.dumps(report, indent=1, default=str))
    print("PASS" if not fails else "FAIL:\n- " + "\n- ".join(fails))
    if note := latency_note(report):
        print(note)
    print(f"report: {path.relative_to(ROOT)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
