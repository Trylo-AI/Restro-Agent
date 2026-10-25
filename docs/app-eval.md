# App evaluation

On 25 Oct 2026 the whole app was tested on the 215 eval cases. Every message went through the real system:

> signed webhook → `api` → work queue → `worker` → router → AI agent (prompt, schema, guardrails) → cart → replies → staff requests

The app is what's being evaluated. The AI model is one component, run on Alibaba's free-tier quota ([decision 17](decisions.md)) with synthetic data only.

Performance of the same runs is in [perf-test.md](perf-test.md).

## Result: 210 of 215 (97.7 %)

| Category | Passed | | Category | Passed |
|---|---|---|---|---|
| Clean English | 25/25 | | Recommendations | 20/20 |
| Hinglish | 39/40 | | Allergen | 20/20 |
| Hindi script | 15/15 | | Sold out | **6/10** |
| Multi-item with notes | 20/20 | | Handoff | 15/15 |
| Ambiguous / misspelt | 25/25 | | Off-topic | 10/10 |
| Edits and cancels | 15/15 | | | |

**Reliability, through two network outages:** 464 customer messages, all processed, all answered. No message was lost, none was dead-lettered, none was answered twice. All 636 replies were delivered ([details](#reliability-two-outages-during-the-run)).

## How it was tested

1. **A fresh customer per case.** Each case gets a new synthetic customer in the isolated load café (`loadtest/seed.py`), already named so the bot doesn't ask for a name.
2. **The customer scans the table QR.** This is the real deterministic turn.
3. **Edit cases start from a cart.** For those cases the expected starting cart is loaded first. This is dev tooling on the synthetic café.
4. **The case message goes through the signed webhook,** exactly as Meta delivers it.
5. **What the app did is read back from the database and scored:**
   - **Cart:** the final cart in `conversations.state`: dishes, quantities, and the expected keyword in the notes.
   - **Sold out:** the dish is never in the cart, **and** the customer is told it's sold out.
   - **Handoff:** a staff request (`requests`) is raised when the customer asks for a person or complains, and only then.
   - **Allergens:** the question is identified, then answered or handed to staff.
   - **Intent:** the AI's intent from `dev_turn_traces`, or the router's keyword when no AI call was made.
   - **Every case:** the customer got a reply, and nothing was dead-lettered.

| | |
|---|---|
| AI | `qwen3.8-max-0902`, with `qwen3.8-flash` as fallback. 210 calls OK, 1 timeout that the fallback answered |
| Worker | `LLM_BACKEND=dashscope`, `DEV_TRACES=true`, 8 customers at a time |
| Tools | `evals/app_run.py`, with the report at `evals/reports/app-20261025-final.json` |

```text
backend/.venv/Scripts/python.exe loadtest/seed.py              # once: the isolated load café
# worker: LLM_BACKEND=dashscope LLM_MODEL_PRIMARY=qwen3.8-max-0902 LLM_MODEL_FALLBACK=qwen3.8-flash
backend/.venv/Scripts/python.exe evals/app_run.py --concurrency 8
backend/.venv/Scripts/python.exe evals/app_run.py --ids v2-sol-2,v2-sol-3   # selected cases
backend/.venv/Scripts/python.exe evals/app_run.py --reuse-from 5           # continue a run that was interrupted
```

## What failed and why

### 1. Sold-out dishes described as "not on the menu": real, 4 of 10

`v2-sol-2` "pesto pasta please", `v2-sol-3` "ek vietnamese coffee", `v2-sol-6` "pasta pesto wala do", `v2-sol-7` "one viet coffee".

| | What the customer saw |
|---|---|
| Expected (6 of 10 cases) | "Pesto Pasta is sold out right now. How about Butter Chicken Bowl, Mushroom Risotto, Thai Green Curry Bowl?" |
| These 4 | "We don't have pesto pasta on our menu. Would you like to try Mushroom Risotto or Thai Green Curry Bowl instead?" |

**Why.** The AI only sees dishes that are available (invariant 2), so it can't know Pesto Pasta exists. The designed path works when the AI passes the customer's words in `cart_ops`: code then finds the sold-out dish and renders the right message. In these 4 cases the AI answered `unclear` in its own words instead. Code never checked those words, so the customer was told something false. Nothing wrong reached the cart. The prompt already tells the AI to pass the customer's words, so this can't rely on the prompt alone.

**Fix (team, critical core `domain/ai_agent`):** before an `unclear` or `browse` reply goes out, code matches the customer's text against today's sold-out dishes, using the same name matcher as the guardrails. On a match, it sends the code-rendered sold-out message with alternatives instead of the AI's text.

### 2. A note kept in Hinglish: disputed, 1 case

- **Case:** `v2-hin-29` "ek cold coffee aur ek brownie, brownie garam karke".
- **What happened:** the brownie's note is "garam karke", which is what the kitchen ticket shows, and staff understand it. The case expects the English keyword "warm".
- **Decision needed:** should notes be written in English? If yes, add a prompt line. If no, the case accepts either word.

## What passed: behaviour worth knowing

- **Handoff (15/15).**
  - "staff please" raises a `help` request with reason `asked`.
  - "this is wrong, my order is cold" raises one with reason `complaint`.
  - In both, the conversation moves to human mode and the customer is told "Someone from our team will be with you shortly."
- **Allergens (20/20).** An `unknown` allergen ("does the brownie have nuts?") raises an `allergy` request. The customer hears "Let me check with the team", then the escalation message after 2 minutes, because nobody acknowledged it: the test café has no staff. The AI never answers an allergen question itself.
- **"One of them" changes are right in the app.** "teen cappuccino, ek mein sugar nahi" became two cart lines, `2 × Cappuccino` and `1 × Cappuccino ↳ no sugar`. The model-only test got this wrong on 5 of 7 models.
- **Misspellings and bare dish names are added,** each followed by the order summary with Confirm: "risoto" → Mushroom Risotto, "cold cofee", "omlet". The same model treated "risoto" as a browse when tested alone. The likely reason is that the app sends the recent chat, where the bot has just asked what they'd like, and the model-only test doesn't.
- **"do avocado toast" gives 2.** "that's all, confirm" with a cart shows the summary with Confirm. "where is my order?" is answered by the router without an AI call.
- **No amount was ever written by the AI.** Every price the customer saw came from code: the order summary and the sold-out alternatives (invariant 1).
- **Sold-out dishes were never in a cart (10/10).**

## Reliability: two outages during the run

This PC lost its connection twice: **09:42–09:48 and 09:57–10:04 UTC**. In both windows nothing reached the database: no webhooks, no turns, no traces. Around them, the app logged:

| Where | Event | Count |
|---|---|---|
| worker | `turn_failed` (asyncpg `InternalClientError`, "release() called on a free connection holder") | 5 |
| worker | `pipeline_crashed` (`ConnectionDoesNotExistError`, `InterfaceError`) | 3 |
| api | `unhandled_error` on a webhook. A 500 to Meta means Meta redelivers, and the wamid dedupes it | 1 |
| local task shim | delivery retries (`ReadTimeout`, `ConnectError`) | 11 |

**Recovery.** Within about a minute of the connection coming back, the backlog was processed: 19 and 23 messages in the next two minutes. The work queue, the leases and the stuck-message sweep did what they're designed to do.

**Integrity across every customer in these runs:**

| Check | Result |
|---|---|
| Customer messages received | 464 |
| Processed | 464 |
| Left without a reply | 0 |
| Dead-lettered | 0 |
| Work still pending or stuck afterwards | 0 |
| Replies sent | 636, all `delivered` |
| Duplicate replies (same message within 3 s) | 0 |
| Staff requests | 22 (15 handoff cases, plus allergen escalations) |

**Effect on the cases.**
- 4 cases were answered late, after the outage, and correctly. They're scored on what the customer finally got.
- 3 cases had their QR turn caught in the outage, so the case message was never sent. Those were run again afterwards.

**Worth an issue (team):** after a dropped connection, asyncpg raised `InternalClientError` inside the app's own pool. The app recovered, but it shows the pool can get into a bad state when a connection dies mid-use. Watch for it in staging. The Phase 3 test of killing a worker mid-run covers the same ground.

## Supporting check: the AI layer alone, on 7 models

Before the app run, the same 215 cases went straight to the model, using the app's prompt and schema but no router, guardrails or cart. This checks how much our prompt and schema depend on the model. All 7 free-tier models returned our format.

| Model | Model-only | Real failures |
|---|---|---|
| qwen3.8-max-0902 | 99.1 % | 0 |
| qwen3.8-27b | 97.7 % | 1 |
| qwen3.8-flash | 97.2 % | 3 |
| deepseek-v4.1-flash-1017 | 96.3 % | 5 |
| deepseek-v4-flash-0731 | 95.8 % | 7 |
| kimi-k3 | 95.8 % | 8 (5 timeouts) |
| deepseek-v4.1-flash | 92.6 % | 14 (12 timeouts) |

**What it says about our app:**
- Without the conversation context the app adds, models stumble on "one of them" changes and on "do" read as English.
- One complaint ("my order is cold") was missed by qwen3.8-flash.
- "staff please" isn't in the router's exact-phrase keyword list, so it relies on the AI.

The app run passed all of these with qwen3.8-max-0902. With a weaker model they would show up in the app too.

Reports: `evals/reports/20261025-08*.json`. Gate metrics: `evals/gate.py "20261025-*.json"`. Run: `evals/run.py --provider dashscope --model <model>`.

**Quota left after both runs:** qwen3.8-max-0902 has used about 709k of its 1M tokens (337k model-only, 372k through the app), so about 290k remain, less than one more full app run. The other six have 550k–680k left each. **deepseek-v4-flash-0731 expires on 30 Oct.**

## Next steps

1. **Team (critical core):**
   - The code check for sold-out dishes in AI replies (failure 1).
   - Add "staff please" to the help keywords.
   - The prompt rule and few-shot for "one of them", a few-shot starting with "do", and a complaint example.
2. **Team:** decide the disputed cases (note language; bare dish names; "something nice" as smalltalk), then update `evals/cases/draft-v2.json`.
3. Re-run `evals/app_run.py` after those changes. It takes about 1.5 h from a laptop and much less on staging.
4. At Phase 3, run the app evaluation on staging with Gemini on Vertex AI. That run sets the production baseline.
