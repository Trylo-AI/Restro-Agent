"""Run N simulated customers through the local stack at once, then check the database (docs/ordering-slice.md#s16).

Taps-only runs never call the model. Real-model runs order by free text ("2 cold coffee and an avocado toast, no
chilli"). Every 4th taps-only run double-taps Confirm. Afterwards, for the customers of this run only:

- one order per Confirm, with exactly the dishes tapped;
- one "Order #N received" per order, and no duplicate outbound rows;
- every inbound message processed, no pending_work left pending or running, no send left pending or ambiguous.

Run with the api and worker up (scripts/dev.ps1). Synthetic numbers only. Prints ids and counts, never message text.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.api.dev.sim import SIM_PHONE_NUMBER_ID  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.pool import close_pool, open_pool  # noqa: E402
from app.db.tx import conn  # noqa: E402

GOAL = "2 cold coffee and 1 avocado toast, no chilli"
NAMES = ["Kabir", "Tara", "Ishaan", "Meher", "Zoya", "Vihaan", "Anaya", "Reyansh", "Nila", "Arush"]


@dataclass
class Run:
    n: int
    real: bool
    wa_id: str = ""
    table: int = 0
    confirms: int = 0
    expected_items: list[str] = field(default_factory=list)  # item ids tapped (taps-only runs)
    order_number: int | None = None
    error: str | None = None
    seconds: float = 0.0


class Sim:
    def __init__(self, client: httpx.AsyncClient, wa_id: str) -> None:
        self.client, self.wa_id, self.seen = client, wa_id, 0

    async def outs(self) -> list[str]:
        r = await self.client.get("/dev/sim/thread", params={"wa_id": self.wa_id})
        r.raise_for_status()
        return [json.dumps(i["payload"], ensure_ascii=False) for i in r.json()["items"] if i["dir"] == "out"]

    async def expect(self, pattern: str, wait_s: float = 45) -> str:
        rx, until = re.compile(pattern), time.monotonic() + wait_s
        while time.monotonic() < until:
            outs = await self.outs()
            for i in range(self.seen, len(outs)):
                if rx.search(outs[i]):
                    self.seen = i + 1
                    return outs[i]
            await asyncio.sleep(1.5)  # each poll costs ~1 s of a pooled connection against the hosted dev DB
        raise TimeoutError(f"no reply matching {pattern!r}")

    async def say(self, text: str) -> None:
        (await self.client.post("/dev/sim/send", json={"wa_id": self.wa_id, "kind": "text", "text": text})).raise_for_status()

    async def tap(self, reply_id: str, list_: bool = False) -> None:
        body = {"wa_id": self.wa_id, "kind": "list" if list_ else "button", "reply_id": reply_id, "reply_title": reply_id[:20]}
        (await self.client.post("/dev/sim/send", json=body)).raise_for_status()


def first_id(payload: str, prefix: str) -> str:
    m = re.search(rf'"id": "({prefix}[^"]+)"', payload)
    if not m:
        raise ValueError(f"no {prefix} button")
    return m.group(1)


def ids(payload: str, prefix: str) -> list[str]:
    return re.findall(rf'"id": "({prefix}[^"]+)"', payload)


async def one(client: httpx.AsyncClient, run: Run) -> None:
    started = time.monotonic()
    try:
        r = await client.post("/dev/sim/customers")
        r.raise_for_status()
        run.wa_id = r.json()["wa_id"]
        sim = Sim(client, run.wa_id)
        run.table = run.n % 12 + 1
        await sim.say(f"Hi, I'm at Table {run.table}")
        await sim.expect(r"What should we call you\?")
        await sim.say(NAMES[run.n % len(NAMES)])
        await sim.expect(r'"id": "menu"')
        if run.real:
            await sim.say(GOAL)
            summary = await sim.expect(r'"id": "confirm:\d+"', wait_s=90)
            if "Cold Coffee" not in summary or "Avocado Toast" not in summary or "2 × Cold Coffee" not in summary:
                raise AssertionError("the model's cart does not match the goal phrase")
        else:
            for pick in range(1 + run.n % 2):  # one or two dishes
                await sim.tap("menu")
                cats = await sim.expect(r'"id": "cat:')
                await sim.tap(ids(cats, "cat:")[run.n % len(ids(cats, "cat:"))], True)
                dishes = await sim.expect(r'"id": "item:')
                item = ids(dishes, "item:")[pick % len(ids(dishes, "item:"))]
                if item not in run.expected_items:
                    run.expected_items.append(item)
                await sim.tap(item, True)
                summary = await sim.expect(r'"id": "confirm:\d+"')
        confirm = first_id(summary, "confirm:")
        await sim.tap(confirm)
        run.confirms = 1
        if not run.real and run.n % 4 == 0:
            await sim.tap(confirm)  # a double tap must not make a second order
            run.confirms = 2
        received = await sim.expect(r"Order #\d+ received")
        run.order_number = int(re.search(r"Order #(\d+) received", received).group(1))  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001  (reported per run)
        run.error = f"{type(e).__name__}: {e}"
    run.seconds = time.monotonic() - started


async def settle(cafe_id: str, customer_ids: list[str], wait_s: float = 60) -> None:
    until = time.monotonic() + wait_s
    while time.monotonic() < until:
        async with conn() as c:
            busy = await c.fetchval(
                """select (select count(*) from inbound_messages i join conversations cv on cv.id = i.conversation_id
                           where i.cafe_id = $1 and cv.customer_id = any($2::uuid[]) and i.processed_at is null)
                        + (select count(*) from pending_work w join conversations cv on cv.id = w.conversation_id
                           where w.cafe_id = $1 and cv.customer_id = any($2::uuid[]) and w.status in ('pending', 'running'))
                        + (select count(*) from wa_messages where cafe_id = $1 and customer_id = any($2::uuid[])
                           and status in ('pending', 'ambiguous'))""",
                cafe_id, customer_ids)
        if busy == 0:
            return
        await asyncio.sleep(1)


async def check(runs: list[Run]) -> list[str]:
    problems: list[str] = []
    async with conn() as c:
        cafe_id = await c.fetchval("select id::text from cafes where phone_number_id = $1", SIM_PHONE_NUMBER_ID)
        rows = await c.fetch("select id::text, wa_id from customers where cafe_id = $1 and wa_id = any($2::text[])",
                             cafe_id, [r.wa_id for r in runs if r.wa_id])
    by_wa = {r["wa_id"]: r["id"] for r in rows}
    await settle(cafe_id, list(by_wa.values()))
    async with conn() as c:
        for run in runs:
            if run.error or run.wa_id not in by_wa:
                continue
            cid = by_wa[run.wa_id]
            orders = await c.fetch("select id::text, number, total_paise from orders where cafe_id = $1 and customer_id = $2",
                                   cafe_id, cid)
            if len(orders) != 1:
                problems.append(f"run {run.n}: {len(orders)} orders for {run.confirms} Confirm tap(s)")
                continue
            order = orders[0]
            if order["number"] != run.order_number:
                problems.append(f"run {run.n}: told #{run.order_number}, stored #{order['number']}")
            items = await c.fetch(
                """select oi.item_id::text, oi.qty, oi.price_snapshot_paise, oi.note, m.name
                   from order_items oi join menu_items m on m.id = oi.item_id where oi.cafe_id = $1 and oi.order_id = $2""",
                cafe_id, order["id"])
            if sum(i["qty"] * i["price_snapshot_paise"] for i in items) != order["total_paise"]:
                problems.append(f"run {run.n}: total is not the sum of its lines")
            if run.real:
                got = {i["name"]: i["qty"] for i in items}
                if got.get("Cold Coffee") != 2 or got.get("Avocado Toast") != 1 or len(got) != 2:
                    problems.append(f"run {run.n}: real-model order is not 2 × Cold Coffee + 1 × Avocado Toast")
                elif not any(i["note"] and "chilli" in i["note"].lower() for i in items if i["name"] == "Avocado Toast"):
                    problems.append(f"run {run.n}: the no-chilli note is missing")
            elif sorted(i["item_id"] for i in items) != sorted(m.removeprefix("item:") for m in run.expected_items):
                problems.append(f"run {run.n}: order lines differ from the dishes tapped")
            received = await c.fetchval(
                """select count(*) from wa_messages where cafe_id = $1 and customer_id = $2
                   and payload::text like '%Order #' || $3::text || ' received%'""", cafe_id, cid, str(order["number"]))
            if received != 1:
                problems.append(f"run {run.n}: {received} 'received' messages")
            dupes = await c.fetchval(
                """select count(*) from (select payload::text, date_trunc('second', created_at) from wa_messages
                   where cafe_id = $1 and customer_id = $2 group by 1, 2 having count(*) > 1) d""", cafe_id, cid)
            if dupes:
                problems.append(f"run {run.n}: {dupes} duplicated outbound message(s)")
            left = await c.fetchrow(
                """select (select count(*) from inbound_messages i join conversations cv on cv.id = i.conversation_id
                           where i.cafe_id = $1 and cv.customer_id = $2 and i.processed_at is null) as inbound,
                          (select count(*) from pending_work w join conversations cv on cv.id = w.conversation_id
                           where w.cafe_id = $1 and cv.customer_id = $2 and w.status in ('pending', 'running')) as work,
                          (select count(*) from wa_messages where cafe_id = $1 and customer_id = $2
                           and status in ('pending', 'ambiguous', 'failed')) as sends""", cafe_id, cid)
            for k in ("inbound", "work", "sends"):
                if left[k]:
                    problems.append(f"run {run.n}: {left[k]} {k} left unfinished")
    return problems


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--real", type=int, default=3, help="extra runs that order by free text through the real model")
    ap.add_argument("--parallel", type=int, default=5)
    args = ap.parse_args()

    s = get_settings()
    if s.app_env != "local" or not s.simulator_on or not s.simulator_token:
        print("Needs APP_ENV=local, SIMULATOR_ON=true and SIMULATOR_TOKEN in backend/.env")
        return 2
    runs = [Run(n, real=False) for n in range(args.runs)] + [Run(args.runs + n, real=True) for n in range(args.real)]
    gate = asyncio.Semaphore(args.parallel)
    await open_pool()
    try:
        async with httpx.AsyncClient(base_url=s.api_url, timeout=20,
                                     headers={"x-sim-token": s.simulator_token.get_secret_value()}) as client:
            async def guarded(run: Run) -> None:
                async with gate:
                    await one(client, run)

            started = time.monotonic()
            await asyncio.gather(*(guarded(r) for r in runs))
            wall = time.monotonic() - started
        problems = await check(runs)
    finally:
        await close_pool()

    for run in runs:
        kind = "real" if run.real else "taps"
        status = run.error or f"order #{run.order_number}"
        print(f"run {run.n:>2} {kind:<4} T{run.table:<2} {run.seconds:5.1f}s  confirms={run.confirms}  {status}")
    failed = [r for r in runs if r.error]
    print(f"\n{len(runs) - len(failed)}/{len(runs)} runs placed an order in {wall:.0f}s wall time")
    for p in problems:
        print("PROBLEM:", p)
    if not problems and not failed:
        print("No lost, duplicate or wrong orders.")
    return 1 if problems or failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
