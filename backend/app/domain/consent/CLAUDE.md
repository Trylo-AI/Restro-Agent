# domain/consent — Module Context (L2) · LEGAL PATH

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [consent](../../../../docs/retention/consent.md) · [data-deletion](../../../../docs/retention/data-deletion.md) · [data-protection](../../../../docs/security/data-protection.md)

## Responsibility

Marketing opt-in and opt-out, the consent register, and recording deletion requests (DPDP).

## States

`Unknown → OptedIn` (Yes after the first order) · `Unknown → Declined` (No) · `Declined → OptedIn` (a later yes, or the counter QR) · `OptedIn → OptedOut` (Stop offers button / `STOP` / blocking marketing in WhatsApp) · `OptedOut → OptedIn` **only by an explicit new opt-in**.

## Must

- **Append-only.** Every change is a new `consents` row with `status`, `wording_version` (the exact text shown), `source` (`order_flow` | `counter_qr` | `feedback_flow`) and `created_at`. The current state is the latest row per `(customer_id, type)`.
- Handle `STOP` and "Stop offers" in **every** stage and mode. They're checked before the AI.
- Don't ask again within N days after a "No". The default is 30 days, which is an open decision.
- Ask at the right moments: after the first order with no record, or after a 4–5 star feedback with no record.
- On "delete my data": create a `data_deletion_requests` row, confirm by text, and let the `data_deletion` job anonymise.

## Must never

- Be inferred by the AI. The AI has no consent tool.
- Overwrite or delete a consent row, except during anonymisation.
- Count `Unknown` or `Declined` as opted in.
