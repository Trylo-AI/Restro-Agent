"""Outbound message model and Meta payload builders, with Meta's limits enforced in code.

Limits (docs/backend.md#sending, **[verify]** against current Meta docs): text ≤ 4096 · interactive body ≤ 1024 ·
≤ 3 reply buttons, title ≤ 20 · list ≤ 10 rows in total, row title ≤ 24, description ≤ 72, button label ≤ 20,
section title ≤ 24 · ids ≤ 256. Names are truncated, never rejected; callers paginate long lists.
"""

from dataclasses import dataclass, field
from typing import Any

TEXT_MAX = 4096
BODY_MAX = 1024
HEADER_MAX = 60
FOOTER_MAX = 60
BUTTON_TITLE_MAX = 20
MAX_BUTTONS = 3
LIST_BUTTON_MAX = 20
ROW_TITLE_MAX = 24
ROW_DESC_MAX = 72
SECTION_TITLE_MAX = 24
MAX_ROWS = 10
ID_MAX = 256


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Button:
    id: str
    title: str


@dataclass(frozen=True)
class Row:
    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True)
class Section:
    title: str
    rows: list[Row]


@dataclass(frozen=True)
class Text:
    body: str

    kind = "text"


@dataclass(frozen=True)
class Buttons:
    body: str
    buttons: list[Button]
    header: str | None = None
    footer: str | None = None

    kind = "interactive"


@dataclass(frozen=True)
class ListMessage:
    body: str
    button: str
    sections: list[Section]
    header: str | None = None
    footer: str | None = None

    kind = "interactive"


@dataclass(frozen=True)
class Template:
    name: str
    params: list[str] = field(default_factory=list)
    language: str = "en"

    kind = "template"


Message = Text | Buttons | ListMessage | Template


def payload(msg: Message) -> dict[str, Any]:
    """Meta Cloud API message body without `to` / `messaging_product` (the client adds those)."""
    if isinstance(msg, Text):
        return {"type": "text", "text": {"body": clip(msg.body, TEXT_MAX), "preview_url": False}}
    if isinstance(msg, Buttons):
        buttons = msg.buttons[:MAX_BUTTONS]
        inter: dict[str, Any] = {
            "type": "button",
            "body": {"text": clip(msg.body, BODY_MAX)},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": b.id[:ID_MAX], "title": clip(b.title, BUTTON_TITLE_MAX)}}
                for b in buttons
            ]},
        }
        _header_footer(inter, msg.header, msg.footer)
        return {"type": "interactive", "interactive": inter}
    if isinstance(msg, ListMessage):
        sections, budget = [], MAX_ROWS
        for s in msg.sections:
            rows = s.rows[:budget]
            budget -= len(rows)
            if rows:
                sections.append({
                    "title": clip(s.title, SECTION_TITLE_MAX),
                    "rows": [
                        {"id": r.id[:ID_MAX], "title": clip(r.title, ROW_TITLE_MAX),
                         **({"description": clip(r.description, ROW_DESC_MAX)} if r.description else {})}
                        for r in rows
                    ],
                })
            if budget == 0:
                break
        inter = {
            "type": "list",
            "body": {"text": clip(msg.body, BODY_MAX)},
            "action": {"button": clip(msg.button, LIST_BUTTON_MAX), "sections": sections},
        }
        _header_footer(inter, msg.header, msg.footer)
        return {"type": "interactive", "interactive": inter}
    if isinstance(msg, Template):
        comp = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in msg.params]}] if msg.params else []
        return {"type": "template", "template": {"name": msg.name, "language": {"code": msg.language}, "components": comp}}
    raise TypeError(type(msg))


def _header_footer(inter: dict[str, Any], header: str | None, footer: str | None) -> None:
    if header:
        inter["header"] = {"type": "text", "text": clip(header, HEADER_MAX)}
    if footer:
        inter["footer"] = {"text": clip(footer, FOOTER_MAX)}


def limit_violations(p: dict[str, Any]) -> list[str]:
    """Checks a built payload against Meta's limits (used by tests and the simulator's red badge)."""
    problems: list[str] = []
    if p.get("type") == "text" and len(p["text"]["body"]) > TEXT_MAX:
        problems.append(f"text body > {TEXT_MAX}")
    inter = p.get("interactive")
    if inter:
        if len(inter["body"]["text"]) > BODY_MAX:
            problems.append(f"body > {BODY_MAX}")
        if inter["type"] == "button":
            buttons = inter["action"]["buttons"]
            if len(buttons) > MAX_BUTTONS:
                problems.append(f"> {MAX_BUTTONS} buttons")
            problems += [f"button title > {BUTTON_TITLE_MAX}" for b in buttons if len(b["reply"]["title"]) > BUTTON_TITLE_MAX]
        if inter["type"] == "list":
            rows = [r for s in inter["action"]["sections"] for r in s["rows"]]
            if len(rows) > MAX_ROWS:
                problems.append(f"> {MAX_ROWS} list rows")
            if len(inter["action"]["button"]) > LIST_BUTTON_MAX:
                problems.append(f"list button > {LIST_BUTTON_MAX}")
            problems += [f"row title > {ROW_TITLE_MAX}" for r in rows if len(r["title"]) > ROW_TITLE_MAX]
            problems += [f"row description > {ROW_DESC_MAX}" for r in rows if len(r.get("description", "")) > ROW_DESC_MAX]
    return problems
