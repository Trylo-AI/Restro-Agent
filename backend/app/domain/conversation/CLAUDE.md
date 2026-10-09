# domain/conversation — Module Context (L2)

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [conversation-router](../../../../docs/backend/conversation-router.md) · [conversation-state-machine](../../../../docs/backend/conversation-state-machine.md) · [inbound-pipeline](../../../../docs/backend/inbound-pipeline.md) · [interactive-messages](../../../../docs/whatsapp/interactive-messages.md)

## Responsibility

The entry point for every inbound customer message once it reaches the worker. It decides **who handles this turn**: a deterministic handler, a fixed step, or the AI agent.

## Routing order (first match wins)

1. `mode = human` → store the message and push it to the staff chat view. **The bot stays silent.**
2. `STOP` text or the "Stop offers" button → consent opt-out, confirmed by text.
   ⚠️ *Open: in the source order, a STOP sent during human mode only reaches staff. The proposal is to check STOP first.*
3. A first message matching "Hi, I'm at Table N" → open or refresh the table session, then greet.
4. An interactive reply → deterministic handler by ID prefix: `menu:` `cat:` `item:` `confirm:` `edit:` `cancel:` `waiter` `bill` `fb:` `consent:`.
5. A stage-specific step: `awaiting_name` → save the name, then greet. `awaiting_feedback_comment` → save the comment.
6. Anything else → an AI agent turn, then guardrails, then send or fall back.

## State

- `stage`: Greeting → AwaitingName → Browsing → CartOpen → AwaitingConfirmation → OrderActive → AwaitingFeedback → AwaitingFeedbackComment → Closed.
- `mode`: `bot` | `human`, an overlay that's independent of `stage`.
- Both are persisted in `conversations`. **Nothing is kept in memory between messages.**

## Must

- Run under the per-conversation advisory lock taken by the pipeline. One message at a time per customer.
- Act on interactive **IDs**, never on visible titles. Reject stale IDs (e.g. an old cart version) politely, and show the current state.
- Send the greeting with buttons Menu · Recommend · Call waiter. "Welcome back" for a known customer.
- On AI timeout (about 12 s) or a second guardrail failure, send the fixed fallback with buttons Menu · Call waiter.
- Make "Call waiter", the `call_human` tool and the AI fallback all go through `handoff`, which sets `mode = human`.

## Must never

- Let the AI place an order. Confirmation is always `cart` + `orders` code.
- Reply as the bot while `mode = human`.
- Read `cafe_id` from message text.

## Open (P2)

Burst coalescing: wait about 1.5 s for follow-up messages before calling the AI. See [inbound-pipeline](../../../../docs/backend/inbound-pipeline.md).
