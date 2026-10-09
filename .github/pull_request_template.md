## What and why

<!-- One or two sentences. Link the doc section this implements. -->

## Context hygiene

- [ ] Behaviour change? I updated the matching doc under `docs/`.
- [ ] Changed an invariant? I updated the nearest `CLAUDE.md` (see `docs/CONTEXT-MAP.md`).
- [ ] Real trade-off decided? I added an ADR in `docs/adr/`.
- [ ] Resolved an open decision? I updated `docs/delivery/open-decisions.md`.

## Safety checks (tick what applies)

- [ ] Money / order state / consent stays in deterministic code, not the AI
- [ ] Every repository call is scoped by `cafe_id`
- [ ] All outbound messages go through `domain/outbound`
- [ ] Idempotent under Meta / Pub/Sub / Tasks redelivery
- [ ] No secrets or full phone numbers in logs
