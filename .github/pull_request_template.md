## What and why

<!-- One or two sentences. Link the doc section this implements, and the build phase. -->

## Docs

- [ ] Behaviour changed → matching section in `docs/` updated.
- [ ] Invariant changed → `CLAUDE.md` updated.
- [ ] Real trade-off → entry in `docs/decisions.md`.
- [ ] Open decision closed → `docs/plan.md` (Open decisions) updated.
- [ ] `contracts/` changed → backend, console and docs updated in this PR.

## Safety (tick what applies)

- [ ] Money, order state and consent stay in deterministic code, not the LLM
- [ ] Money in integer paise
- [ ] Every repository call is scoped by `cafe_id`
- [ ] All outbound messages go through `domain/outbound`
- [ ] Idempotent under Meta retries, Cloud Tasks retries and double taps
- [ ] No secrets, tokens or phone numbers in code, fixtures or logs
- [ ] Tests added; assumptions listed below

## Assumptions

<!-- What you assumed that isn't in the docs. -->
