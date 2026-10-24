# Realtime events

Contract. Source: [Realtime](../docs/data-and-api.md#realtime). Only these tables are in the `supabase_realtime` publication, each with an RLS read policy. The console joins one channel per table, filtered `cafe_id=eq.<active café>`, with the user's JWT.

| Table | Events | Version field | Drives |
|---|---|---|---|
| `orders` | INSERT, UPDATE | `version` | Order cards, bill totals, badges, chime, alarms |
| `requests` | INSERT, UPDATE | `updated_at` | Calls and bills, Chats badges, help alarm |
| `conversations` | UPDATE | `updated_at` | Chats list, human-mode badge |
| `inbound_messages` | INSERT | — | Open chat thread |
| `wa_messages` | INSERT, UPDATE | `updated_at` | Open chat thread, delivery ticks |
| `menu_items` | UPDATE | `version` | Sold-out toggles on every device |
| `cafes` | UPDATE | `updated_at` | Paused and Disconnected banners |
| `notifications` | INSERT, UPDATE | `updated_at` | Bell badge and list (`manager`+, own rows) |
| `customer_notes` | INSERT, UPDATE | `updated_at` | Profile comments and their applied changes |
| `customer_preferences` | INSERT, UPDATE, DELETE | `updated_at` | Profile box; "On profile" allergy on order cards |

Rules (console, `src/lib/realtime`):

1. Subscribe first, buffer events, do the initial read, then apply the buffer. No gap.
2. Apply an UPDATE only if its version field is newer than the row shown.
3. `order_items` isn't published: on an order INSERT, or an UPDATE whose `flags` gained `edited`, read the items.
4. Refetch active orders and open requests on reconnect, tab visible again, café switch, and every 30 s while connected.
5. DELETE events carry only the primary key (`customer_preferences` uses replica identity full so the console can drop the chip).
6. The console is never the only alert path; escalation timers send `new_order_alert` regardless.
