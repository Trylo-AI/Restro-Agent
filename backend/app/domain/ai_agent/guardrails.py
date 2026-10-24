"""Guardrails on model output (docs/backend.md#guardrails). Pure functions, unit-tested."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.db.repositories.menu import MenuItem

ALLERGENS_FILE = Path(__file__).resolve().parents[4] / "contracts" / "allergens.json"

# ₹ · Rs / Rs. · INR · rupee(s) / rupaye / rupay · /- · "190 rs" — any of these in model text is an amount.
AMOUNT = re.compile(
    r"(₹|\brs\.?(?=\s*\d)|\binr\b|\brupe(e|es|ya|ye|y)\b|\brupay|/-|\d+\s*(rs|inr|rupees?|rupaye|rupay)\b|\bprice\s+is\s+\d)",
    re.IGNORECASE,
)
REFUND = re.compile(r"(refund|money back|paise wapas|paisa wapas|compensat|voucher|free (meal|dish|drink|item)|discount)",
                    re.IGNORECASE)
ALLERGY_MENTION = re.compile(r"(allerg|एलर्जी|intoleran)", re.IGNORECASE)


def has_amount(text: str | None) -> bool:
    return bool(text and AMOUNT.search(text))


def has_refund_promise(text: str | None) -> bool:
    return bool(text and REFUND.search(text))


def clean_reply(text: str | None, limit: int = 600) -> str | None:
    """Model text that may reach the customer: trimmed, capped, and dropped entirely if it holds an amount."""
    if not text:
        return None
    text = " ".join(text.split())[:limit]
    return None if has_amount(text) else (text or None)


@lru_cache
def allergen_table() -> list[tuple[str, str, frozenset[str]]]:
    """(id, label, every name it goes by) for the 11 allergens."""
    out = []
    for a in json.loads(ALLERGENS_FILE.read_text(encoding="utf-8"))["allergens"]:
        names = frozenset({str(a["id"]).replace("_", " "), str(a["label"]).lower(), *(str(x) for x in a["aliases"])})
        out.append((str(a["id"]), str(a["label"]), names))
    return out


def normalise_allergen(words: str | None) -> tuple[str, str] | None:
    """'nuts' → ('tree_nuts', 'Tree nuts'); 'dairy' → ('milk', 'Milk'). None when it isn't one of the 11."""
    if not words:
        return None
    w = words.lower().strip().replace("-", " ")
    for allergen_id, label, names in allergen_table():
        if w in names or w.rstrip("s") in names or any(n in w.split() for n in names if " " not in n):
            return allergen_id, label
    return None


def allergens_in(text: str) -> list[str]:
    """Allergen labels mentioned together with an allergy word ("I'm allergic to peanuts")."""
    if not ALLERGY_MENTION.search(text):
        return []
    found = []
    low = text.lower()
    for _, label, names in allergen_table():
        if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
            found.append(label.lower())
    return found


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+", s.lower()) if len(w) > 2}


def match_by_name(words: str, items: list[MenuItem]) -> MenuItem | None:
    """Best single dish whose name shares the most significant words with the customer's words."""
    wanted = _words(words)
    if not wanted:
        return None
    scored = sorted(((len(wanted & _words(it.name)), it) for it in items), key=lambda x: -x[0])
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None
