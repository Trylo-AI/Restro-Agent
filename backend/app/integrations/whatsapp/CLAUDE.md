# integrations/whatsapp — Module Context (L2)

> Parent: [backend/CLAUDE.md](../../../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [docs/whatsapp/](../../../../docs/whatsapp/README.md) — [webhooks](../../../../docs/whatsapp/webhooks.md) · [outbound-sender](../../../../docs/whatsapp/outbound-sender.md) · [interactive-messages](../../../../docs/whatsapp/interactive-messages.md) · [templates](../../../../docs/whatsapp/templates.md) · [number-health](../../../../docs/whatsapp/number-health.md)

## Responsibility

A thin, typed adapter over the Meta WhatsApp Cloud API. **No business rules**: the 24h window and consent checks live in `domain/outbound`.

| Planned file | Does |
|---|---|
| `client.py` | `POST /{phone_number_id}/messages`, mark-as-read, typing indicator, template management, debug-token. Token comes from `gcp/secrets` (cached 10 min) |
| `parser.py` | Webhook payload → typed events: `message`, `status`, `template_update`, `quality_update` |
| `builders.py` | Payloads for text, reply buttons, list messages and templates |
| `errors.py` | Meta error codes → typed errors (retryable 429/5xx, auth/token, `131049` per-user marketing cap, blocked, …) |

## Must

- Parse **every** entry and change in a webhook batch. One POST can carry several messages and statuses.
- Expose `phone_number_id` and `wa_id` on every parsed event. They drive tenant resolution and the ordering key.
- Treat status updates as **monotonic** (`sent → delivered → read`, or `failed`), and surface the `pricing` (billable, category) for cost tracking.
- Interactive replies: return the button/list **ID**. Handlers act on the ID, never the visible title.
- Classify errors so the caller can tell retry, give up, and alert token health apart.

## Must never

- Be called directly from anything except `domain/outbound`. Read-only health and template calls from `domain` platform services are the exception.
- Log access tokens or full `wa_id`s.
- Hard-code Graph API versions or limits without a config entry. Meta facts are mid-2026, so verify them before building.

## Tests that must stay green

Parser fixtures for each event type and for multi-entry batches · builder payload snapshots · error-code classification.
