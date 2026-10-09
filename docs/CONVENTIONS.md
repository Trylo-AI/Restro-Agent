# Documentation conventions

How every file under `docs/` is written, so that humans and AI coding agents can find, trust and update them.

## 1. Doc header (every file)

Each doc starts with a title and a header block:

```text
# <Title>

> **Area:** <Backend | AI agent | WhatsApp | Retention | Frontend | Database | API | Infra | Security | Observability | Product | Architecture | Delivery>
> **Source:** MVP Architecture v0.1 §<n.n> (link to ../_source/mvp-architecture-v0.1.md)
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** <the CLAUDE.md context files that link here>
```

Then, in this order (skip a section only if it truly doesn't apply):

1. **Summary** — 2–4 sentences: what this is and why it matters.
2. **Body** — the facts, tables and diagrams.
3. **Rules / invariants** — the must-always / must-never statements an implementer cannot break.
4. **Open questions** — anything undecided (link to [delivery/open-decisions.md](delivery/open-decisions.md) when it's tracked there).
5. **Related** — relative links to sibling docs and the context files that load this doc.

## 2. Content rules

- **Source of truth.** Content is derived from [`_source/mvp-architecture-v0.1.md`](_source/mvp-architecture-v0.1.md). Keep every fact, table and diagram from the assigned section. Don't drop details while splitting.
- **No code.** These are design docs. Allowed: Mermaid diagrams, tables, text trees, field and endpoint names in backticks. Not allowed: Python, TypeScript, SQL or shell snippets.
- **Mark additions.** Anything that isn't in the source (an extra diagram, an elaboration, a proposal) is fine, but proposals that change behaviour are tagged **(proposed)** and listed under *Open questions*. Explanatory diagrams that only restate the source need no tag.
- **One topic per file.** If a file passes ~250 lines, split it.
- **Links are relative** (`../database/data-model.md`, `../../backend/CLAUDE.md`).
- **Names match the code-to-be.** Use the exact table, column, endpoint, job, queue and template names from the source (`order_status_events`, `/staff/orders/{id}/status`, `overdue_sweep`, `wa-inbound`, `new_order_alert`).

## 3. Mermaid rules (GitHub-renderable)

- Use `flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`, `classDiagram`, `journey`, `gantt` or `mindmap`.
- Quote node labels that contain punctuation: `A["Order → SERVED"]`. Use `<br/>` for line breaks inside labels.
- No parentheses, braces or colons inside **unquoted** labels.
- In `erDiagram`, attribute types are single words (`text_array`, not `text[]`).
- In `sequenceDiagram` messages, avoid semicolons; `{` `}` are fine.
- Keep diagrams under ~40 nodes; split into two if larger.
- Add one sentence under each diagram saying what to look at.

## 4. Context levels

Docs are the **L3 reference** layer. They are read on demand, never loaded automatically. See [CONTEXT-MAP.md](CONTEXT-MAP.md) for how `CLAUDE.md` files (L0–L2) point into them.

When you change behaviour:

1. Update the relevant doc here.
2. If it changes an invariant, update the `CLAUDE.md` that states it.
3. If it is a real decision with trade-offs, add an ADR in [`adr/`](adr/README.md).
