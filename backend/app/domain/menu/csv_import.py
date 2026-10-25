"""Menu CSV parsing and validation (docs/backend.md#csv-import). Pure: no DB. All or nothing: any invalid row
rejects the file, with every row and column error listed.

Required: name, category, price (rupees), prep_minutes, veg (yes/no). Optional: description, spice (0–3),
contains, free_from (allergen ids separated by `;`, the open-decision default), available (yes/no, default yes).
"""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

ALLERGENS = ("gluten", "milk", "eggs", "peanuts", "tree_nuts", "soy", "sesame", "fish", "shellfish", "mustard", "sulphites")
REQUIRED = ("name", "category", "price", "prep_minutes", "veg")
OPTIONAL = ("description", "spice", "contains", "free_from", "available")
MAX_BYTES = 1_000_000
MAX_ROWS = 500
TEMPLATE = ("name,category,price,prep_minutes,veg,description,spice,contains,free_from,available\n"
            "Cold Coffee,Cold coffee,190,5,yes,Iced and creamy,0,milk,gluten;eggs,yes\n")
YES, NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}


@dataclass
class Row:
    line: int
    name: str
    category: str
    price_paise: int
    prep_minutes: int
    veg: bool
    description: str | None
    spice: int
    available: bool
    allergens: dict[str, str] = field(default_factory=dict)

    def fields(self) -> dict[str, object]:
        return {"name": self.name, "description": self.description, "category": self.category,
                "price_paise": self.price_paise, "prep_minutes": self.prep_minutes, "veg": self.veg,
                "spice": self.spice, "available": self.available}


@dataclass
class Parsed:
    rows: list[Row]
    errors: list[dict[str, object]]


def _yes_no(value: str, default: bool | None = None) -> bool | None:
    v = value.strip().lower()
    if not v:
        return default
    return True if v in YES else False if v in NO else None


def _allergens(value: str) -> tuple[list[str], list[str]]:
    ids = [a.strip().lower().replace(" ", "_") for a in value.split(";") if a.strip()]
    return [a for a in ids if a in ALLERGENS], [a for a in ids if a not in ALLERGENS]


def parse(text: str) -> Parsed:
    errors: list[dict[str, object]] = []

    def err(line: int, column: str, message: str) -> None:
        errors.append({"row": line, "column": column, "message": message})

    if len(text.encode("utf-8")) > MAX_BYTES:
        return Parsed([], [{"row": 0, "column": "", "message": "The file is larger than 1 MB."}])
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    unknown = [c for c in header if c and c not in REQUIRED + OPTIONAL]
    if missing or unknown:
        for c in missing:
            err(1, c, "Required column is missing.")
        for c in unknown:
            err(1, c, "Unknown column.")
        return Parsed([], errors)
    rows: list[Row] = []
    seen: dict[str, int] = {}
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            err(n, "", f"More than {MAX_ROWS} rows.")
            break
        if raw.get(None):
            err(n, "", "More values than columns: put values that contain commas in quotes.")
            continue
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
        if not any(r.values()):
            continue
        name, category = r.get("name", ""), r.get("category", "")
        if not 1 <= len(name) <= 60:
            err(n, "name", "Name must be 1–60 characters.")
        elif name.lower() in seen:
            err(n, "name", f"Same name as row {seen[name.lower()]}.")
        else:
            seen[name.lower()] = n
        if not 1 <= len(category) <= 40:
            err(n, "category", "Category must be 1–40 characters.")
        price_paise = 0
        try:
            rupees = Decimal(r.get("price", "").replace("₹", "").replace(",", "").strip())
            price_paise = int(rupees * 100)
            if rupees * 100 != price_paise or price_paise <= 0:
                raise InvalidOperation
        except InvalidOperation:
            err(n, "price", "Price must be in rupees, more than 0, at most 2 decimals.")
        prep = r.get("prep_minutes", "")
        if not (prep.isdigit() and 1 <= int(prep) <= 120):
            err(n, "prep_minutes", "Prep time must be a whole number of minutes, 1–120.")
        veg = _yes_no(r.get("veg", ""))
        if veg is None:
            err(n, "veg", "Veg must be yes or no.")
        spice_raw = r.get("spice", "") or "0"
        if not (spice_raw.isdigit() and int(spice_raw) <= 3):
            err(n, "spice", "Spice must be 0, 1, 2 or 3.")
        available = _yes_no(r.get("available", ""), True)
        if available is None:
            err(n, "available", "Available must be yes or no.")
        description = r.get("description") or None
        if description and len(description) > 200:
            err(n, "description", "Description must be 200 characters or fewer.")
        contains, bad_c = _allergens(r.get("contains", ""))
        free_from, bad_f = _allergens(r.get("free_from", ""))
        for col, bad in (("contains", bad_c), ("free_from", bad_f)):
            if bad:
                err(n, col, f"Unknown allergen: {', '.join(bad)}. Use: {', '.join(ALLERGENS)}, separated by ;")
        both = set(contains) & set(free_from)
        if both:
            err(n, "free_from", f"Listed as both contains and free from: {', '.join(sorted(both))}.")
        rows.append(Row(line=n, name=name, category=category, price_paise=price_paise,
                        prep_minutes=int(prep) if prep.isdigit() else 0, veg=bool(veg), description=description,
                        spice=int(spice_raw) if spice_raw.isdigit() else 0, available=bool(available),
                        allergens={**{a: "contains" for a in contains}, **{a: "free_from" for a in free_from}}))
    if not rows and not errors:
        err(2, "", "The file has no rows.")
    return Parsed(rows if not errors else [], errors)
