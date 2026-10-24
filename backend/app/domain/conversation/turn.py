"""What a turn handler receives and returns. Handlers decide; the pipeline commits and sends."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.db.repositories.cafes import Cafe
from app.db.repositories.conversations import Conversation
from app.db.repositories.customers import Customer
from app.db.repositories.inbound import InboundMessage
from app.domain.outbound import Purpose
from app.integrations.whatsapp.messages import Message, Template


@dataclass
class Turn:
    """One unit of work: a single tap, a single non-text message, or consecutive texts joined."""
    kind: str                         # tap · text · other
    messages: list[InboundMessage]

    @property
    def first_id(self) -> str:
        return self.messages[0].id

    @property
    def text(self) -> str:
        return "\n".join(str((m.payload.get("text") or {}).get("body", "")) for m in self.messages if m.type == "text").strip()

    @property
    def reply_id(self) -> str | None:
        inter = self.messages[0].payload.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        if not reply and self.messages[0].payload.get("button"):
            return str(self.messages[0].payload["button"].get("payload"))
        return str(reply["id"]) if reply.get("id") else None

    @property
    def reply_title(self) -> str | None:
        inter = self.messages[0].payload.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("title")


@dataclass
class Reply:
    message: Message
    purpose: str = Purpose.REPLY
    fallback_template: Template | None = None


# Runs inside the turn's fenced transaction; may return replies that need values only known there
# (e.g. the order number). Those replies are prepared in the same transaction.
CommitFn = Callable[[asyncpg.Connection], Awaitable["list[Reply] | None"]]
AfterFn = Callable[[], Awaitable[None]]


@dataclass
class TurnContext:
    cafe: Cafe
    conversation: Conversation
    customer: Customer


@dataclass
class TurnResult:
    state: dict[str, Any] | None = None          # new conversations.state (None = unchanged)
    replies: list[Reply] = field(default_factory=list)
    commit: list[CommitFn] = field(default_factory=list)   # extra writes in the same fenced transaction
    after: list[AfterFn] = field(default_factory=list)     # after commit and sends (timers, signals)


Handler = Callable[[TurnContext, Turn], Awaitable[TurnResult]]
