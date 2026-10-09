# domain/outbound — Module Context (L2)

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [outbound-sender](../../../../docs/whatsapp/outbound-sender.md) · [interactive-messages](../../../../docs/whatsapp/interactive-messages.md) · [templates](../../../../docs/whatsapp/templates.md) · [cost-tracking](../../../../docs/observability/cost-tracking.md)

## Responsibility

**The single function every customer- or staff-bound message passes through.** Owning it in one place is what makes the 24-hour rule, consent and logging impossible to bypass.

## Decision order

1. **Not a template?** It needs `now − customer.last_inbound_at < 24 h`, otherwise reject it ("needs a template") and let the caller decide, e.g. fall back to the `order_update` template.
2. **Marketing template?** It needs current consent = `OptedIn`, otherwise reject it ("no consent"). Utility templates skip this check.
3. Build the payload (text / buttons / list / template), get the café token (Secret Manager, cached 10 min) and `POST /{phone_number_id}/messages`.
4. On `2xx`, insert a `messages` row (`direction = out`, `wamid`). On `429/5xx`, retry with backoff, at most 3 times. On any other `4xx`, store `error_code`. An auth error alerts token health.

## Also owns

Message builders for the greeting, dish lists rendered from the DB, the cart summary, confirmation buttons, status updates, the fallback, the consent ask, the feedback list, and mark-as-read plus the typing indicator.

## Must never

- Be skipped. No other module may call `integrations/whatsapp` to send.
- Send marketing to anyone who isn't `OptedIn`, or free text outside the window.
- Put a price or dish name into a message that didn't come from the DB.
