"""Recommendation candidates and the code ranking (docs/backend.md#recommendations). Critical core: allergen rules.

Code decides which dishes may be suggested; the model (ai_agent/recommend.py) only orders them and writes a short
reason. Hard filters, in order: available · allergies (profile + this visit: a known allergen keeps only dishes
marked `free_from`, so `contains` and `unknown` both leave; any other word removes dishes whose name or description
holds it) · diet · dislikes · spice · cart and already shown · cap 60. Allergies never reach a prompt, and nothing
here claims a dish is vegan, Jain or allergen-free.
"""

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from app.db.repositories import menu as menu_repo
from app.db.repositories import preferences as preferences_repo
from app.db.repositories import recommend as recommend_repo
from app.db.repositories.menu import MenuItem
from app.db.tx import conn
from app.domain import hours as hours_mod
from app.domain.ai_agent import guardrails, prompt
from app.domain.conversation.turn import TurnContext

# code → (row title, the fixed phrase the prompt gets)
MOODS: dict[str, tuple[str, str]] = {
    "light": ("Something light", "something light"),
    "energy": ("Need a pick-me-up", "a pick-me-up"),
    "cool": ("Something cold", "something cold and refreshing"),
    "comfort": ("Comfort food", "comfort food"),
    "sweet": ("Something sweet", "something sweet"),
}
MAX_PICKS = 3
MAX_SHOWN = 12
HISTORY_DAYS = 180
POPULAR_DAYS = 30
POPULAR_TTL_S = 600
MAX_PROMPT_CANDIDATES = 60
MAX_PER_CATEGORY = 2
MAX_FAVOURITES = 2
QUICK_MINUTES = 10

# A mood boosts dishes whose category or name starts with one of these words.
MOOD_WORDS: dict[str, tuple[str, ...]] = {
    "light": ("light", "salad", "toast", "tea", "fruit", "smoothie", "granola", "snack", "soup", "poha", "upma"),
    "energy": ("coffee", "espresso", "chai", "brew", "shot", "energy", "mocha"),
    "cool": ("cold", "iced", "ice", "shake", "cooler", "smoothie", "frappe", "lemonade", "juice", "chilled"),
    "comfort": ("main", "curry", "pasta", "risotto", "sandwich", "chocolate", "chai", "soup", "comfort", "khichdi"),
    "sweet": ("dessert", "bake", "sweet", "brownie", "cake", "chocolate", "pastr", "cookie", "tiramisu", "banoffee"),
}
# Typed moods (the turn model's short summary) → a code mood, for the fallback boost only.
MOOD_HINTS: dict[str, tuple[str, ...]] = {
    "cool": ("cold", "thanda", "thandi", "iced", "chill", "cool", "refresh", "hot day", "garmi"),
    "sweet": ("sweet", "meetha", "dessert", "sugar", "craving chocolate"),
    "energy": ("energy", "tired", "thak", "sleepy", "neend", "awake", "pick-me-up", "pick me up", "boost"),
    "light": ("light", "halka", "halki", "small", "not hungry", "snack"),
    "comfort": ("comfort", "cosy", "cozy", "sad", "rainy", "baarish", "filling", "hungry", "bhook"),
}
VEG_DIETS = {"veg", "vegetarian", "vegan", "jain", "eggetarian"}
ROOT_WORDS = ("onion", "garlic", "potato", "aloo", "pyaz", "pyaaz", "lahsun", "ginger", "adrak", "carrot", "beetroot",
              "beet", "radish", "mooli", "shakarkand", "yam", "arbi")
SPICE_CAP = {"0": 1, "1": 2}  # profile spice → the hottest dish kept
INJECTION = re.compile(r"(ignore|system|prompt|instruction|rule)", re.IGNORECASE)
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass(frozen=True)
class HistoryItem:
    item_id: str
    times: int      # orders it was in
    qty: int
    days_ago: int


@dataclass(frozen=True)
class Pick:
    item: MenuItem
    why: str
    source: str     # llm · favourite · popular · other


@dataclass
class RecContext:
    items: list[MenuItem]                              # available only (the turn view)
    allergen_states: dict[str, dict[str, str]]         # item_id → allergen → state; no row = unknown
    prefs: list[tuple[str, str]]                       # (kind, value), allergy included (filters only)
    visit_allergies: list[str]                         # state.allergy_note
    history: list[HistoryItem]
    popular: list[str]                                 # item ids, most sold first
    cart_ids: list[str]
    local_time: datetime
    near_closing: bool
    first_name: str | None
    language: str                                      # English · Hindi (the tap path, decision 20)
    view: prompt.MenuView | None = None


@dataclass
class CandidateSet:
    items: list[MenuItem]
    excluded: dict[str, str] = field(default_factory=dict)  # item_id → sold_out · allergy · diet · dislike · spice · cart · shown
    allergy_filtered: bool = False

    @property
    def ids(self) -> set[str]:
        return {it.id for it in self.items}


# ── context ───────────────────────────────────────────────────────────────

_allergen_cache: dict[str, tuple[int, dict[str, dict[str, str]]]] = {}   # cafe_id → (menu_version, states)
_popular_cache: dict[str, tuple[float, list[str]]] = {}                    # cafe_id → (monotonic at, ids)


async def _allergen_states(c: asyncpg.Connection, cafe_id: str, view: prompt.MenuView) -> dict[str, dict[str, str]]:
    cached = _allergen_cache.get(cafe_id)
    if cached and cached[0] == view.version:
        return cached[1]
    states = await menu_repo.allergens(c, cafe_id, [it.id for it in view.items])
    _allergen_cache[cafe_id] = (view.version, states)
    return states


async def _popular(c: asyncpg.Connection, cafe_id: str) -> list[str]:
    cached = _popular_cache.get(cafe_id)
    if cached and time.monotonic() - cached[0] < POPULAR_TTL_S:
        return cached[1]
    ids = await recommend_repo.popular_items(c, cafe_id, POPULAR_DAYS)
    _popular_cache[cafe_id] = (time.monotonic(), ids)
    return ids


def cart_ids(state: dict[str, Any]) -> list[str]:
    edit = state.get("order_edit")
    cart = edit["cart"] if edit else state.get("cart") or {}
    return [str(line["item_id"]) for line in cart.get("items", [])]


def language_of(text: str | None) -> str:
    return "Hindi" if text and DEVANAGARI.search(text) else "English"


async def load_context(ctx: TurnContext, state: dict[str, Any]) -> RecContext:
    """Reads only; no transaction is held (the LLM call comes after)."""
    view = await prompt.menu_view(ctx.cafe)
    async with conn() as c:
        states = await _allergen_states(c, ctx.cafe.id, view)
        prefs = await preferences_repo.all_for(c, ctx.cafe.id, ctx.customer.id)
        history = [HistoryItem(*row) for row in
                   await recommend_repo.item_history(c, ctx.cafe.id, ctx.customer.id, HISTORY_DAYS)]
        popular = await _popular(c, ctx.cafe.id)
        last = await recommend_repo.last_text(c, ctx.cafe.id, ctx.conversation.id)
    h = hours_mod.status(ctx.cafe)
    return RecContext(
        items=list(view.items), allergen_states=states, prefs=prefs,
        visit_allergies=[x for x in str(state.get("allergy_note") or "").split(", ") if x],
        history=history, popular=popular, cart_ids=cart_ids(state),
        local_time=datetime.now(ZoneInfo(ctx.cafe.timezone or "Asia/Kolkata")), near_closing=h.near_closing,
        first_name=(ctx.customer.name or "").split(" ")[0] or None, language=language_of(last), view=view,
    )


# ── hard filters ──────────────────────────────────────────────────────────

def _word_pattern(word: str) -> re.Pattern[str]:
    """Whole word, singular or plural: "mushrooms" matches "mushroom" and "Mushrooms"."""
    w = " ".join(word.lower().split())
    stems = {w}
    if w.endswith("es") and len(w) > 3:
        stems.add(w[:-2])
    if w.endswith("s") and len(w) > 2:
        stems.add(w[:-1])
    return re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(stems, key=len, reverse=True)) + r")(e?s)?\b")


def _text(it: MenuItem) -> str:
    return f"{it.name} {it.description or ''}".lower()


def _single(prefs: list[tuple[str, str]], kind: str) -> str | None:
    values = [v for k, v in prefs if k == kind]
    return values[-1].strip().lower() if values else None


def filter_candidates(rc: RecContext, *, exclude: frozenset[str] = frozenset()) -> CandidateSet:
    allergies = [v for k, v in rc.prefs if k == "allergy"] + list(rc.visit_allergies)
    known: set[str] = set()
    words: list[re.Pattern[str]] = []
    for a in allergies:
        norm = guardrails.normalise_allergen(a)
        if norm:
            known.add(norm[0])
        elif a.strip():
            words.append(_word_pattern(a))
    diet = (_single(rc.prefs, "diet") or "").replace("_", " ")
    max_spice = SPICE_CAP.get(_single(rc.prefs, "spice") or "")
    dislikes = [_word_pattern(v) for k, v in rc.prefs if k == "dislike" and v.strip()]
    roots = [_word_pattern(w) for w in ROOT_WORDS] if diet == "jain" else []
    cart = set(rc.cart_ids)

    cs = CandidateSet(items=[], allergy_filtered=bool(known or words))
    for it in rc.items:
        states = rc.allergen_states.get(it.id, {})
        text = _text(it)
        if not it.available:
            reason = "sold_out"
        elif any(states.get(a) != "free_from" for a in known) or any(p.search(text) for p in words):
            reason = "allergy"
        elif (diet in VEG_DIETS and not it.veg) or (diet == "vegan" and "contains" in (states.get("milk"), states.get("eggs"))) \
                or any(p.search(text) for p in roots):
            reason = "diet"
        elif any(p.search(text) for p in dislikes):
            reason = "dislike"
        elif max_spice is not None and it.spice > max_spice:
            reason = "spice"
        elif it.id in cart:
            reason = "cart"
        elif it.id in exclude:
            reason = "shown"
        else:
            cs.items.append(it)
            continue
        cs.excluded[it.id] = reason
    if len(cs.items) > MAX_PROMPT_CANDIDATES:
        keep = {it.id for it in sorted(cs.items, key=_score_key(rc))[:MAX_PROMPT_CANDIDATES]}
        cs.items = [it for it in cs.items if it.id in keep]
    return cs


def _name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(' '.join(name.lower().split()))}(?!\w)")


def names_other_dish(text: str | None, items: list[MenuItem], picks: list[MenuItem]) -> bool:
    """True when model text names a menu dish that is not one of the picks (whole name, any case). Such a dish may
    have been filtered out (allergy, diet, dislike, cart), so the text is dropped and code writes its own. The picks'
    own names are removed first, so "Iced Latte" never counts as a "Latte" on the menu."""
    if not text:
        return False
    low = " ".join(text.lower().split())
    for it in sorted(picks, key=lambda it: len(it.name), reverse=True):
        low = _name_pattern(it.name).sub(" ", low)
    keep = {it.id for it in picks}
    return any(it.id not in keep and _name_pattern(it.name).search(low) for it in items)


def _score_key(rc: RecContext) -> Any:
    times = {h.item_id: h.times for h in rc.history}
    rank = {iid: n for n, iid in enumerate(rc.popular)}
    return lambda it: (-times.get(it.id, 0), rank.get(it.id, len(rank)), it.category, it.name)


# ── code ranking ──────────────────────────────────────────────────────────

def mood_matches(mood: str | None, it: MenuItem) -> bool:
    words = MOOD_WORDS.get(mood or "", ())
    text = f"{it.category} {it.name}".lower()
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def guess_mood(text: str | None) -> str | None:
    """The code mood a typed-mood summary is closest to (fallback boost only)."""
    low = (text or "").lower()
    return next((code for code, hints in MOOD_HINTS.items() if any(h in low for h in hints)), None)


def _favourite_why(h: HistoryItem, last_days: int | None) -> str:
    if h.times > 1:
        return f"You've had this {h.times} times"
    return "You had this last time" if h.days_ago == last_days else "You've had this before"


def code_why(rc: RecContext, item: MenuItem) -> str:
    h = next((x for x in rc.history if x.item_id == item.id), None)
    if h is not None:
        return _favourite_why(h, min(x.days_ago for x in rc.history))
    if item.id in rc.popular:
        return "Popular here this week"
    return f"From our {item.category.lower()}"


def fallback_rank(rc: RecContext, cs: CandidateSet, *, mood: str | None, n: int = MAX_PICKS,
                  taken: tuple[str, ...] | list[str] = ()) -> list[Pick]:
    """Favourites (≤ 2, by times then recency) → popular → one per category in name order; ≤ 2 per category.
    A mood puts its dishes first; near closing, quick dishes (≤ 10 min) lead within each tier."""
    skip = set(taken)
    pool = {it.id: it for it in cs.items if it.id not in skip}
    if not pool or n <= 0:
        return []
    last_days = min((h.days_ago for h in rc.history), default=None)
    tiers: list[tuple[int, MenuItem, str, str]] = []
    favourites = [h for h in sorted(rc.history, key=lambda h: (-h.times, h.days_ago)) if h.item_id in pool]
    for h in favourites[:MAX_FAVOURITES]:
        tiers.append((0, pool[h.item_id], _favourite_why(h, last_days), "favourite"))
    for iid in rc.popular:
        if iid in pool:
            tiers.append((1, pool[iid], "Popular here this week", "popular"))
    by_cat: dict[str, list[MenuItem]] = {}
    for it in cs.items:
        if it.id in pool:
            by_cat.setdefault(it.category, []).append(it)
    rounds = [sorted(v, key=lambda it: it.name) for v in by_cat.values()]
    for r in range(max((len(v) for v in rounds), default=0)):
        for items in rounds:
            if r < len(items):
                tiers.append((2 + r, items[r], f"From our {items[r].category.lower()}", "other"))

    def key(entry: tuple[int, MenuItem, str, str]) -> tuple[int, int, int]:
        tier, it, _, _ = entry
        quick = 0 if (not rc.near_closing or it.prep_minutes <= QUICK_MINUTES) else 1
        return (0 if not mood or mood_matches(mood, it) else 1, tier, quick)

    ordered: list[tuple[int, MenuItem, str, str]] = []
    seen: set[str] = set()
    for entry in sorted(tiers, key=key):  # stable: the tier order stays within equal keys
        if entry[1].id not in seen:
            seen.add(entry[1].id)
            ordered.append(entry)
    picks: list[Pick] = []
    per_cat: dict[str, int] = {}
    for _, it, why, source in ordered:
        if len(picks) >= n:
            break
        if per_cat.get(it.category, 0) >= MAX_PER_CATEGORY:
            continue
        per_cat[it.category] = per_cat.get(it.category, 0) + 1
        picks.append(Pick(it, why, source))
    for _, it, why, source in ordered:  # a menu with too few categories: fill up anyway
        if len(picks) >= n:
            break
        if all(p.item.id != it.id for p in picks):
            picks.append(Pick(it, why, source))
    return picks


def merge(llm: list[Pick], cs: CandidateSet, fallback: list[Pick], n: int = MAX_PICKS) -> list[Pick]:
    """Model picks that are still candidates, topped up from the code ranking to min(n, candidates); no duplicates."""
    allowed = cs.ids
    out: list[Pick] = []
    for p in llm + fallback:
        if len(out) >= min(n, len(allowed)):
            break
        if p.item.id in allowed and all(o.item.id != p.item.id for o in out):
            out.append(p)
    return out


# ── text that may reach a prompt ──────────────────────────────────────────

def _letters(text: str) -> str:
    """Letters (any script, with their vowel signs), spaces, commas and hyphens; everything else becomes a space."""
    kept = "".join(ch if ch.isalpha() or unicodedata.category(ch).startswith("M") or ch in " ,-" else " " for ch in text)
    return " ".join(kept.split()).strip(" ,-")


def sanitise_mood(text: str | None) -> str | None:
    """A typed-mood summary for state and the "Other ideas" prompt: letters only, ≤ 60; None on an amount or an
    instruction word."""
    if not text or guardrails.has_amount(text) or INJECTION.search(text):
        return None
    return _letters(text)[:60].strip(" ,-") or None


def safe_text(text: str | None, limit: int) -> str | None:
    """Staff- or chat-sourced text (first name, preference values) for a prompt: kept only when it is letters,
    spaces, hyphens and apostrophes, ≤ limit, and holds no instruction word."""
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) > limit or INJECTION.search(text):
        return None
    ok = all(ch.isalpha() or unicodedata.category(ch).startswith("M") or ch in " '-" for ch in text)
    return text if ok and text.strip(" '-") else None
