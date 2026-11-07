"""Customer profile: staff comments, AI-applied changes, undo and manual removal (decision 16).

Comments are stored word for word; the worker reads each one with one LLM call (`profile_note` task) and applies
the validated ops in one transaction, allergies included. Every change is audited and shown under its comment
with Undo. A comment never changes consent.
"""

from typing import Any

from app.core.auth import Principal
from app.core.errors import NotFound, VersionConflict
from app.core.logging import get_logger
from app.db.repositories import cafes, customers
from app.db.repositories import preferences as prefs
from app.db.repositories.preferences import Note
from app.db.tx import conn, tx
from app.domain import audit
from app.domain.ai_agent import profile_note
from app.integrations import tasks

log = get_logger(__name__)


async def _customer(c: Any, p: Principal, customer_id: str) -> None:
    if await customers.get(c, p.cafe_id, customer_id) is None:
        raise NotFound()


async def add_note(p: Principal, customer_id: str, text: str) -> Note:
    async with tx() as c:
        await _customer(c, p, customer_id)
        note = await prefs.insert_note(c, p.cafe_id, customer_id, p.user_id, text)
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "customer.note",
                          {"customer_id": customer_id, "note_id": note.id, "chars": len(text)})
    await tasks.tasks().enqueue("profile-note", {"cafe_id": p.cafe_id, "note_id": note.id}, name=f"note:{note.id}")
    return note


async def process_note(cafe_id: str, note_id: str) -> str:
    """Worker task. Re-reads the note; only a `pending` note is processed (safe to run twice)."""
    async with conn() as c:
        note = await prefs.get_note(c, cafe_id, note_id)
        cafe = await cafes.get(c, cafe_id)
        current = await prefs.all_for(c, cafe_id, note.customer_id) if note else []
    if note is None or cafe is None or note.ai_status != "pending":
        return "skipped"
    read = await profile_note.interpret(cafe_id=cafe_id, cafe_model=cafe.settings.get("model"), note_id=note_id,
                                        text=note.text, current=current)
    async with tx() as c:
        if read is None:
            await prefs.set_note_result(c, cafe_id, note_id, "failed", [], None)
            return "failed"
        ops, summary = read
        applied = await _apply(c, cafe_id, note.customer_id, note_id, ops)
        status = "applied" if applied else "no_changes"
        done = await prefs.set_note_result(c, cafe_id, note_id, status, applied, summary if applied else None)
        if done and applied:
            await audit.write(c, cafe_id, audit.Actor(note.author_id, "staff_via_ai"), "customer.profile_update",
                              {"customer_id": note.customer_id, "note_id": note_id,
                               "ops": [{"op": o["op"], "kind": o["kind"], "value": o["value"]} for o in applied]})
    return status


async def _apply(c: Any, cafe_id: str, customer_id: str, note_id: str, ops: list[dict[str, str]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for op in ops:
        kind, value = op["kind"], op["value"]
        if kind in profile_note.SINGLE:
            current = await prefs.values_of(c, cafe_id, customer_id, kind)
            if op["op"] == "set":
                if current == [value]:
                    continue
                await prefs.delete_kind(c, cafe_id, customer_id, kind)
                await prefs.insert(c, cafe_id, customer_id, kind, value, "staff_note", note_id)
                applied.append({**op, "previous": current[0] if current else None})
            elif op["op"] == "remove" and value in current:
                await prefs.delete_value(c, cafe_id, customer_id, kind, value)
                applied.append({**op, "previous": value})
        elif op["op"] == "add":
            if await prefs.insert(c, cafe_id, customer_id, kind, value, "staff_note", note_id):
                applied.append({**op, "previous": None})
        elif op["op"] == "remove":
            if await prefs.delete_value(c, cafe_id, customer_id, kind, value):
                applied.append({**op, "previous": value})
    return applied


def _touches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["kind"] != b["kind"]:
        return False
    return a["kind"] in profile_note.SINGLE or a["value"] == b["value"]


async def undo_note(p: Principal, note_id: str) -> Note:
    """Reverse one comment's applied changes; 409 if a later comment changed the same value."""
    async with tx() as c:
        note = await prefs.get_note(c, p.cafe_id, note_id, for_update=True)
        if note is None:
            raise NotFound()
        if note.undone_at is not None or note.ai_status != "applied":
            return note
        later = await prefs.later_applied_notes(c, p.cafe_id, note.customer_id, note.created_at)
        if any(_touches(op, other) for other_note in later for other in other_note.ai_ops for op in note.ai_ops):
            raise VersionConflict("Changed again since — remove it from the box instead.")
        for op in reversed(note.ai_ops):
            kind, value, previous = op["kind"], op["value"], op.get("previous")
            if op["op"] == "set":
                await prefs.delete_value(c, p.cafe_id, note.customer_id, kind, value)
                if previous:
                    await prefs.insert(c, p.cafe_id, note.customer_id, kind, previous, "staff_note", None)
            elif op["op"] == "add":
                await prefs.delete_value(c, p.cafe_id, note.customer_id, kind, value)
            elif op["op"] == "remove":
                await prefs.insert(c, p.cafe_id, note.customer_id, kind, value, "staff_note", None)
        undone = await prefs.mark_undone(c, p.cafe_id, note_id, p.user_id)
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "customer.profile_undo",
                          {"customer_id": note.customer_id, "note_id": note_id,
                           "ops": [{"op": o["op"], "kind": o["kind"], "value": o["value"]} for o in note.ai_ops]})
    return undone or note


async def remove_preference(p: Principal, customer_id: str, kind: str, value: str) -> list[tuple[str, str]]:
    """The chip ×: remove one value by hand."""
    async with tx() as c:
        await _customer(c, p, customer_id)
        if await prefs.delete_value(c, p.cafe_id, customer_id, kind, value):
            await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "customer.profile_update",
                              {"customer_id": customer_id, "ops": [{"op": "remove", "kind": kind, "value": value}],
                               "by_hand": True})
        return await prefs.all_for(c, p.cafe_id, customer_id)
