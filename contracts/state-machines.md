# State machines

Contract. Sources: [orders](../docs/backend.md#orders), [conversation](../docs/backend.md#conversation), [sending](../docs/backend.md#sending), [pipeline](../docs/architecture.md#message-pipeline). Code implements these tables literally; anything not listed is forbidden.

## Order status

`orders.status`: `NEW` · `ACCEPTED` · `READY` · `SERVED` · `CANCELLED`. Only `transition()` changes it, with `expected_version` (optimistic lock). Every change writes one `order_events` row; staff changes also write `audit_log`.

| From | To | Actor | Minimum role | Guard | Customer hears | Timer after commit |
|---|---|---|---|---|---|---|
| — | `NEW` | customer (Confirm) | — | unique `idempotency_key` | at once: "Order #N received — about X min" | `accept_escalation_1` 60 s |
| — | `ACCEPTED` | staff (manual order) | `shift_manager` | `Idempotency-Key` header | nothing | `overdue` at due |
| `NEW` | `NEW` | customer (edit) | — | version; flag `edited` | at once: "Updated order #N" | — |
| `NEW` | `CANCELLED` | customer | — | only `NEW`; `cancel_reason_code = customer` | at once: "Order #N cancelled." | — |
| `NEW` | `ACCEPTED` | staff | `shift_manager` | 409 if the customer changed it first | +5 s: "Your order is being made" | `overdue` at due |
| `NEW` | `CANCELLED` | staff | `shift_manager` | reason code (+ text for `other`); notifications | +5 s: AI-drafted cancellation | — |
| `ACCEPTED` | `READY` | staff | `shift_manager` | — | +5 s: "Your order is ready and on its way" | `auto_served` 10 min |
| `ACCEPTED` | `CANCELLED` | staff | `manager` | reason code (+ text); notifications | +5 s: AI-drafted cancellation | — |
| `READY` | `SERVED` | staff, or system (`auto_served`) | `shift_manager` | — | nothing | `feedback` (Phase 5) |
| `READY` | `CANCELLED` | staff | `manager` | reason code (+ text); notifications | +5 s: AI-drafted cancellation | — |
| any | previous | same staff actor | same as the move | within 5 s; reason `undo` | the queued +5 s message is never sent | the undone move's timers no-op |

Statuses never skip; nothing follows `SERVED` or `CANCELLED`. Disallowed move → 422 `invalid_transition`; stale version → 409 `version_conflict` with the current row.

**Flags** (`orders.flags`, never statuses): `escalated` · `overdue` · `delayed` · `edited` · `item_sold_out` · `near_closing`.

## Conversation

### Steps (`conversations.state.step`)

| Step | Entered when |
|---|---|
| `greeting` | First message of a visit, or QR text |
| `browsing` | Name known; cart empty |
| `building_cart` | An item is added; any change at `confirming`; ordering more after `order_placed` or `post_order` |
| `confirming` | Cart summary with Confirm · Edit · Cancel sent |
| `order_placed` | Confirm created an order (`NEW`) |
| `post_order` | The visit's order is `SERVED` |

Back-edges: cart cleared or Cancel → `browsing`; Edit or a change at `confirming` → `building_cart`; next visit → `greeting`.

`state.awaiting`: `null` · `name` · `table` · `rating` · `feedback_text`. The next text answers it with no LLM call.

### Mode (`conversations.mode`), an overlay on the step

| From | To | Trigger |
|---|---|---|
| `bot` | `human` | Handoff (`asked`, `repeated_unclear`, `complaint`, `frustration`, `allergen_unknown`, `system_error`) · staff Take over · staff reply · Message a table · dead-lettered turn |
| `human` | `bot` | Hand back · resolving the conversation's `help` or `allergy` request · `idle_handback` (15 min, no unanswered customer message) |

In `human` the bot sends nothing except the STOP reply; the step doesn't change.

## Work queue (`pending_work.status`)

| From | To | When |
|---|---|---|
| — | `pending` | Webhook stores an inbound message (text due +1 s, taps due now); a new text moves `next_attempt_at` to now + 1 s |
| `pending` | `running` | A worker holding the conversation lease claims the due row |
| `running` | `done` | Turn committed and its sends attempted |
| `running` | `pending` | Turn failed: `attempts` + 1, `next_attempt_at` + 2 / 4 / 8 s, `work_retry` timer |
| `running` | `dead_lettered` | 4th failure (3 retries): standard customer message, `help` + human mode, alert |
| `dead_lettered` | `pending` | `platform_admin` Retry (`attempts` reset) |
| `dead_lettered` | `resolved` | `platform_admin` Resolve |

One `pending` row per (`conversation_id`, `kind`). A `running` row whose lease expired is reclaimed by `stuck_inbound_check`.

**Lease** (`conversations.lease_owner`, `lease_expires_at`): claim when null or expired (30 s), renew every 10 s, every commit in a turn checks `lease_owner` (fencing), release on exit.

## Outbound step (`wa_messages.status`)

| From | To | When |
|---|---|---|
| — | `pending` | Row written before the request (`idempotency_key` unique) |
| `pending` | `sent` | Meta accepted; `wamid` stored |
| `pending` | `pending` | Not sent: connection failed before the request left, 429, `130429` (retry with backoff) |
| `pending` | `ambiguous` | Timeout or 5xx after the request left (`send_check` at 60 s) |
| `pending` | `failed` | Other 4xx, `190`, or retries exhausted |
| `ambiguous` | `sent` | Status webhook names the row |
| `ambiguous` | `failed` | No status webhook by `send_check` |
| `sent` | `delivered` → `read` | Status webhooks, forward only |
| `sent` / `delivered` | `failed` | Status webhook `failed` |

Never backwards; `read` and `failed` are final.

## Requests (`requests.status`)

`open` → `acknowledged` (`acknowledged_by`, `acknowledged_at`) → `resolved` (`resolved_at`); `open` → `resolved` directly is allowed. One unresolved request per (`conversation_id`, `kind`).

## Customer note (`customer_notes.ai_status`)

`pending` → `applied` (ops stored in `ai_ops`) · `pending` → `no_changes` · `pending` → `failed`. Undo sets `undone_at` / `undone_by` on an `applied` note; it never returns to `pending`.
