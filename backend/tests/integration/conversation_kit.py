"""Drive real turns: signed-webhook-equivalent ingest → pipeline → recorded sends (no network, real DB)."""

import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.db.repositories import pending_work as pw
from app.db.repositories.cafes import Cafe
from app.domain.conversation import agent_turn, ingress, pipeline, router
from app.integrations import tasks
from app.integrations.whatsapp import client as wa_client
from app.integrations.whatsapp import simulated
from app.integrations.whatsapp.parser import parse

MENU = [
    # name, category, rupees, prep, veg, allergens
    ("Cold Coffee", "Cold coffee", 190, 4, True, {"milk": "contains"}),
    ("Avocado Toast", "Toasts", 320, 8, True, {"gluten": "contains"}),
    ("Chocolate Brownie", "Desserts", 180, 3, True, {"tree_nuts": "unknown"}),
    ("Masala Chai", "Tea", 90, 5, True, {"milk": "contains"}),
]


@dataclass
class Sent:
    to: str
    body: dict[str, Any]

    @property
    def text(self) -> str:
        if self.body.get("type") == "text":
            return str(self.body["text"]["body"])
        if self.body.get("type") == "interactive":
            return str(self.body["interactive"]["body"]["text"])
        return f"template:{self.body['template']['name']}"

    @property
    def ids(self) -> list[str]:
        inter = self.body.get("interactive") or {}
        if inter.get("type") == "button":
            return [b["reply"]["id"] for b in inter["action"]["buttons"]]
        if inter.get("type") == "list":
            return [r["id"] for s in inter["action"]["sections"] for r in s["rows"]]
        return []


class RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[Sent] = []

    async def send(self, cafe: Cafe, to: str, body: dict[str, Any], callback_data: str) -> str:
        self.sent.append(Sent(to, body))
        return f"wamid.T.{len(self.sent)}.{callback_data[:6]}"

    async def mark_read(self, cafe: Cafe, wamid: str, wa_id: str) -> None:
        return None


class TaskRecorder:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict[str, Any], float]] = []

    async def publish(self, cafe_id: str, conversation_id: str) -> None:
        return None

    async def enqueue(self, queue: str, body: dict[str, Any], *, delay_s: float = 0, name: str | None = None) -> None:
        self.tasks.append((queue, body, delay_s))

    def timers(self, kind: str) -> list[dict[str, Any]]:
        return [b for q, b, _ in self.tasks if q == "timers" and b.get("kind") == kind]


@dataclass
class Kitchen:
    db: asyncpg.Pool
    cafe_id: str
    phone_number_id: str
    items: dict[str, str]          # name → id
    tables: dict[str, str]         # label → id
    transport: RecordingTransport
    rec: TaskRecorder
    staff: dict[str, str] = field(default_factory=dict)  # role → user_id

    async def say(self, wa_id: str, text: str | None = None, *, tap: str | None = None, kind: str = "text") -> list[Sent]:
        """One customer message, processed to the end. Returns what the bot sent back."""
        if tap is not None:
            body, _ = simulated.message_payload(self.phone_number_id, wa_id, None, "interactive",
                                                reply_id=tap, reply_title=tap)
        elif kind != "text":
            body, _ = simulated.message_payload(self.phone_number_id, wa_id, None, kind)
        else:
            body, _ = simulated.message_payload(self.phone_number_id, wa_id, None, "text", text=text)
        before = len(self.transport.sent)
        for ev in parse(body).messages:
            await ingress.ingest_message(ev)
        conv_id = await self.conversation_id(wa_id)
        await pipeline.run(self.cafe_id, conv_id)
        return self.transport.sent[before:]

    async def conversation_id(self, wa_id: str) -> str:
        return str(await self.db.fetchval(
            """select cv.id::text from conversations cv join customers c on c.id = cv.customer_id
               where cv.cafe_id = $1 and c.wa_id = $2""", self.cafe_id, wa_id))

    async def state(self, wa_id: str) -> dict[str, Any]:
        return dict(await self.db.fetchval(
            """select cv.state from conversations cv join customers c on c.id = cv.customer_id
               where cv.cafe_id = $1 and c.wa_id = $2""", self.cafe_id, wa_id))

    async def orders(self) -> list[asyncpg.Record]:
        return list(await self.db.fetch(
            "select id::text, number, status, total_paise, version, flags, customer_id::text, allergy_note "
            "from orders where cafe_id = $1 order by number", self.cafe_id))


def install(transport: RecordingTransport, rec: TaskRecorder) -> None:
    wa_client.set_transport(transport)
    tasks.set_backends(signal_impl=rec, tasks_impl=rec)
    pipeline.set_handler(router.route)
    router.set_free_text_handler(agent_turn.handle_text)
    pw.DEBOUNCE_SECONDS = 0.0  # tests drive turns directly


def uninstall() -> None:
    from app.integrations import llm

    llm.set_client(None)
    wa_client.set_transport(None)
    tasks.set_backends()
    pw.DEBOUNCE_SECONDS = 1.0


async def build_kitchen(conn: asyncpg.Connection, cafe_id: str) -> tuple[dict[str, str], dict[str, str]]:
    items = {}
    for name, cat, rupees, prep, veg, allergens in MENU:
        version = await conn.fetchval("select next_menu_version($1)", cafe_id)
        item_id = await conn.fetchval(
            """insert into menu_items (cafe_id, name, category, price_paise, prep_minutes, veg, version)
               values ($1, $2, $3, $4, $5, $6, $7) returning id::text""",
            cafe_id, name, cat, rupees * 100, prep, veg, version)
        for allergen, st in allergens.items():
            await conn.execute("insert into menu_item_allergens (cafe_id, item_id, allergen, state) values ($1, $2, $3, $4)",
                               cafe_id, item_id, allergen, st)
        items[name] = item_id
    tables = {}
    for n in (1, 2, 5):
        tables[f"T{n}"] = await conn.fetchval(
            "insert into tables (cafe_id, label, qr_text) values ($1, $2, $3) returning id::text",
            cafe_id, f"T{n}", f"Hi, I'm at Table {n}")
    await conn.execute("update cafes set settings = settings || '{\"alert_phone\": \"919999000900\"}'::jsonb where id = $1",
                       cafe_id)
    return items, tables


async def create_staff(conn: asyncpg.Connection, cafe_id: str) -> dict[str, str]:
    """Throwaway auth users + memberships (deleted with the café by drop_staff)."""
    staff = {}
    for role, name, phone in (("shift_manager", "Ravi", None), ("manager", "Kiran", "919999000802"),
                              ("owner", "Neha", "919999000803")):
        uid = str(uuid.uuid4())
        await conn.execute("insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
                           uid, f"test-{uid[:12]}@restro.test")
        await conn.execute("insert into memberships (user_id, cafe_id, role, display_name, alert_phone) values ($1, $2, $3, $4, $5)",
                           uid, cafe_id, role, name, phone)
        staff[role] = uid
    return staff


async def drop_staff(conn: asyncpg.Connection, staff: dict[str, str]) -> None:
    await conn.execute("delete from auth.users where id = any($1::uuid[]) and email like 'test-%@restro.test'",
                       list(staff.values()))
