"""Guardrails on model output (docs/backend.md#guardrails). Pure functions, unit-tested."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.db.repositories.menu import MenuItem

ALLERGENS_FILE = Path(__file__).resolve().parents[4] / "contracts" / "allergens.json"

# ₹ · Rs / Rs. · INR · rupee(s) / rupaye / rupay · /- · "190 rs" · रुपये / रुपए / रुपया · "१९० रु" — any of these in
# model text is an amount. `\d` matches Devanagari digits too.
AMOUNT = re.compile(
    r"(₹|\brs\.?(?=\s*\d)|\binr\b|\brupe(e|es|ya|ye|y)\b|\brupay|/-|\d+\s*(rs|inr|rupees?|rupaye|rupay)\b|\bprice\s+is\s+\d"
    r"|रुपय|रुपए|रुपये|रुपया|रूपय|रूपए|रूपये|\d+\s*रु|रु\.?\s*\d)",
    re.IGNORECASE,
)
# What a recommendation line must never claim (decision 20): diet, health, allergen or safety promises, and offers.
CLAIM = re.compile(
    r"(\bvegan\b|\bjain\b|\bhealth|\bnutri|\bprotein|\ballerg|\bsafe|\bdiet\b|\bguilt|\blow[- ]?(cal|fat|sugar|carb)"
    r"|\bdiscount|\boffers?\b|\bdeals?\b|\bcash ?back|\bvoucher|\bcoupon|एलर्जी|सुरक्षित|हेल्दी|स्वस्थ|मुफ़्त|मुफ्त)",
    re.IGNORECASE,
)
URL = re.compile(r"(https?://|\bwww\.|\b[\w-]+\.(com|in|net|org|io|co|app|link|ly|me|shop)\b)", re.IGNORECASE)
FREE = re.compile(r"(\bfree\b|\bmuft\b)", re.IGNORECASE)  # "nut-free", "free dessert", "muft"
DIGIT = re.compile(r"\d")  # any Unicode decimal digit: ASCII and Devanagari
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


def has_claim(text: str | None) -> bool:
    return bool(text and (CLAIM.search(text) or URL.search(text) or FREE.search(text)))


def clean_why(text: str | None, limit: int = 80) -> str | None:
    """A model-written recommendation line (a `why`, or the intro with limit 160): trimmed and capped, or None when
    it holds any digit, amount, claim, URL or "free". Stricter than `clean_reply`: a bare "190" is a price here."""
    if not text or not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text or DIGIT.search(text) or has_amount(text) or has_claim(text):
        return None
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return text


@lru_cache
def allergen_table() -> list[tuple[str, str, frozenset[str]]]:
    """(id, label, every name it goes by) for the 11 allergens."""
    out = []
    for a in json.loads(ALLERGENS_FILE.read_text(encoding="utf-8"))["allergens"]:
        names = frozenset({str(a["id"]).replace("_", " "), str(a["label"]).lower(), *(str(x) for x in a["aliases"])})
        out.append((str(a["id"]), str(a["label"]), names))
    return out


def normalise_allergen(words: str | None) -> tuple[str, str] | None:
    """'nuts' → ('tree_nuts', 'Tree nuts'); 'dairy' → ('milk', 'Milk'); a stored id ('tree_nuts') → itself. None when
    it isn't one of the 11."""
    if not words:
        return None
    w = words.lower().strip().replace("-", " ").replace("_", " ")
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
