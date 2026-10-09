# Frontend — Area Context (L1)

> Loaded when working under `frontend/`. Inherits every invariant in [/CLAUDE.md](../CLAUDE.md).
> Full design: [docs/frontend/](../docs/frontend/README.md) · API contract: [docs/api/](../docs/api/README.md)

## What lives here

**One** React + Vite + TypeScript SPA, hosted on Firebase Hosting, with three role-based areas:

| Area | Users | Device | Doc |
|---|---|---|---|
| Staff panel | `staff`, `manager`, `owner` | Phone or tablet first | [staff-panel.md](../docs/frontend/staff-panel.md) |
| Owner console | `owner` (some screens `manager`) | Browser | [owner-console.md](../docs/frontend/owner-console.md) |
| Platform admin | `platform_admin` | Browser | [platform-admin.md](../docs/frontend/platform-admin.md) |

Customers never use the SPA. They live entirely in WhatsApp.

## Layout

```text
src/
├── app/          routes, role-based layout, café switcher, guards
├── components/   shared UI primitives (no data fetching)
├── features/     one folder per screen group → features/CLAUDE.md
│   ├── orders/ waiter/ chat/ menu/ redeem/          staff
│   ├── campaigns/ customers/ analytics/ settings/   owner
│   └── platform/                                    platform admin
└── lib/          supabase client (auth + realtime only), api client, realtime hooks, sound alerts
public/           static assets (alert sounds, icons)
```

## Frontend invariants

1. **Supabase directly only for (a) login and (b) Realtime reads.** Every write, including status changes, sold-out toggles, replies and redemptions, goes through FastAPI. RLS has no write policies, so a direct write fails by design.
2. Only the **anon key** ships in the bundle. Never the service-role key, never a WABA token.
3. Every API call sends `Authorization: Bearer <Supabase JWT>` and `X-Cafe-Id`. The backend checks membership. Hiding a button in the UI is UX, not security.
4. Realtime subscriptions are limited to `orders`, `order_items`, `waiter_requests` and `messages` (the human-mode chat). After a reconnect, **refetch** over REST, because Realtime events can be missed.
5. New-order and waiter-call alerts must be **loud**: sound plus vibration plus a card pinned to the top. Assume the tab may be backgrounded. The server-side backup is a WhatsApp alert after 60 s.
6. Phone numbers are always shown **masked**.
7. Prices and totals shown come from the API. Never compute them in the browser.

Detail: [app-structure.md](../docs/frontend/app-structure.md) · [realtime-and-alerts.md](../docs/frontend/realtime-and-alerts.md)

## Task → doc

| Task | Doc |
|---|---|
| Routes, guards, data-access rules | [app-structure.md](../docs/frontend/app-structure.md) |
| Live orders, waiter queue, chat, sold-out, redeem | [staff-panel.md](../docs/frontend/staff-panel.md) |
| Menu, tables/QR, campaigns, customers, analytics | [owner-console.md](../docs/frontend/owner-console.md) |
| Onboarding, templates, health, costs, audit | [platform-admin.md](../docs/frontend/platform-admin.md) |
| Realtime, alerts, reconnect | [realtime-and-alerts.md](../docs/frontend/realtime-and-alerts.md) |
| Which endpoint, which role | [docs/api/staff.md](../docs/api/staff.md) · [admin.md](../docs/api/admin.md) · [platform.md](../docs/api/platform.md) |
| Role permissions | [roles-and-permissions.md](../docs/product/roles-and-permissions.md) |
