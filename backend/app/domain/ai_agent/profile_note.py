"""Read one staff comment into profile ops (docs/backend.md#customer-profile-and-staff-comments).

The model proposes `{ops, summary}`; `validate()` keeps only ops on the fixed kinds with clean values. Applying them
is the customers domain's job. The prompt carries the comment and the current profile only: no phone, wa_id,
orders or other customers.
"""

import re
from typing import Any

from app.domain.ai_agent import calls, guardrails

SINGLE = {"diet", "milk", "sugar", "spice", "seating"}
LIST = {"allergy", "dislike", "like", "other"}
DIETS = {"veg": "veg", "vegetarian": "veg", "vegan": "vegan", "jain": "jain", "eggetarian": "eggetarian",
         "eggitarian": "eggetarian", "non_veg": "non_veg", "non veg": "non_veg", "nonveg": "non_veg",
         "non-vegetarian": "non_veg", "non vegetarian": "non_veg"}
SPICE = {"0": "0", "none": "0", "no spice": "0", "not spicy": "0", "1": "1", "mild": "1", "less": "1", "2": "2",
         "medium": "2", "3": "3", "hot": "3", "very spicy": "3", "extra spicy": "3"}
FORBIDDEN = re.compile(r"(offer|promo|discount|consent|unsubscribe|opt[ -]?(in|out)|marketing|stop messag|price|₹|\brs\b|"
                       r"rupee|order #|\bbill\b|phone|number)", re.IGNORECASE)

SYSTEM = """You update a café customer's profile from ONE comment written by café staff.
Return JSON {"ops": [...], "summary": "..."}. Each op is {"op": "set"|"add"|"remove", "kind": ..., "value": ...}.
Kinds with ONE value (use "set" or "remove"): diet (veg, vegan, jain, eggetarian, non_veg), milk (e.g. oat, almond,
skimmed), sugar (e.g. none, less, extra), spice (0-3), seating (e.g. window, outdoor).
Kinds with MANY values (use "add" or "remove"): allergy (one of: gluten, milk, eggs, peanuts, tree nuts, soy, sesame,
fish, shellfish, mustard, sulphites — else the words as written), dislike, like, other (short facts worth remembering).
Rules: only what the comment clearly states about the customer's tastes, diet or allergies. Values at most 40
characters, lowercase. Never record anything about offers, messages, consent, prices, payments or orders. If the
comment says something no longer applies, use "remove". Nothing to change → empty ops.
summary: a short human-readable list of the changes, like "Diet → vegan · Dislikes + mushrooms"."""


def validate(raw_ops: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_ops if isinstance(raw_ops, list) else []:
        if not isinstance(raw, dict):
            continue
        op, kind, value = raw.get("op"), raw.get("kind"), " ".join(str(raw.get("value") or "").split()).lower()
        if kind not in SINGLE | LIST or op not in ("set", "add", "remove") or not value or len(value) > 40:
            continue
        if FORBIDDEN.search(value):
            continue
        if kind in SINGLE and op == "add":
            op = "set"
        if kind in LIST and op == "set":
            op = "add"
        if kind == "diet":
            value = DIETS.get(value.replace("-", " ") if value not in DIETS else value, "")
            if not value:
                continue
        if kind == "spice":
            value = SPICE.get(value, "")
            if not value:
                continue
        if kind == "allergy":
            norm = guardrails.normalise_allergen(value)
            value = norm[0] if norm else value
        key = (op, kind, value)
        if key not in seen:
            seen.add(key)
            out.append({"op": op, "kind": kind, "value": value})
    return out[:12]


async def interpret(*, cafe_id: str, cafe_model: str | None, note_id: str, text: str,
                    current: list[tuple[str, str]]) -> tuple[list[dict[str, str]], str | None] | None:
    """(ops, summary), or None when the LLM failed twice (the note is marked failed; the profile is unchanged)."""
    profile = "; ".join(f"{k}: {v}" for k, v in current) or "nothing yet"
    attempted = await calls.call(
        cafe_id=cafe_id, cafe_model=cafe_model, purpose="profile_note", system=SYSTEM,
        prompt=f"CURRENT PROFILE: {profile}\nSTAFF COMMENT: {text}", schema_name="profile_note",
        max_output_tokens=400, note_id=note_id,
    )
    if attempted is None:
        return None
    data = attempted.result.data or {}
    summary = data.get("summary") if isinstance(data.get("summary"), str) else None
    summary = guardrails.clean_reply(summary, 200)
    return validate(data.get("ops")), summary
