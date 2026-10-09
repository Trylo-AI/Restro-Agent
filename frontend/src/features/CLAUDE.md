# frontend/src/features — Module Context (L2)

> Parent: [frontend/CLAUDE.md](../../CLAUDE.md) · Root: [/CLAUDE.md](../../../CLAUDE.md)
> Docs: [staff-panel](../../../docs/frontend/staff-panel.md) · [owner-console](../../../docs/frontend/owner-console.md) · [platform-admin](../../../docs/frontend/platform-admin.md) · [api/](../../../docs/api/README.md)

## One folder per screen group

| Folder | Area | Min role | Main endpoints | Realtime |
|---|---|---|---|---|
| `orders/` | Staff | `staff` | `GET /staff/orders`, `PATCH /staff/orders/{id}/status`, `POST …/cancel` (manager) | `orders`, `order_items` |
| `waiter/` | Staff | `staff` | `GET /staff/waiter-requests`, `POST …/ack`, `…/resolve` | `waiter_requests` |
| `chat/` | Staff | `staff` | `GET/POST /staff/conversations/{id}/…`, `takeover`, `release` | `messages` |
| `menu/` | Staff / Owner | `staff` (toggle) · `manager` (edit) | `PATCH /staff/menu/items/{id}/availability`, `/admin/menu/*` | — |
| `redeem/` | Staff | `staff` | `POST /staff/redeem` | — |
| `campaigns/` | Owner | `owner` | `/admin/campaigns*`, `/admin/templates` | — |
| `customers/` | Owner | `owner` | `/admin/customers*`, `/admin/consents*` | — |
| `analytics/` | Owner | `manager` | `/admin/analytics/*`, `/admin/reports/weekly` | — |
| `settings/` | Owner | `owner` | `/admin/settings`, `/admin/tables*`, `/admin/staff*` | — |
| `platform/` | Platform | `platform_admin` | `/platform/*` | — |

## Rules for every feature

- Read through `lib/` hooks only. Features never create their own Supabase or HTTP clients.
- Writes go to FastAPI only. Optimistic UI is fine, but reconcile with the API response and the Realtime echo.
- A feature is self-contained (screens, hooks, local components). Promote shared UI to `src/components/`.
- Guard routes by role in `src/app/`, and still handle a `403` from the API gracefully.
- Staff screens are designed for a phone in one hand during a rush: big tap targets, two taps max for sold-out and status changes.
