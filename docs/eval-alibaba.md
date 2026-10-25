# AI accuracy test on Alibaba free-tier models

Run on 25 Oct 2026 against 7 models on Alibaba Cloud Model Studio (Singapore), using their free-tier quota. All 215 eval cases ran on each model.

**For testing only.**
- Only synthetic cases are sent.
- The app is unchanged and production stays on Gemini on Vertex AI ([decisions](decisions.md)).
- The Alibaba client lives in `evals/` and is never imported by `backend/app`.

## What this tests

**This is accuracy, not load.** Each case is one customer message plus the right answer. The runner gives the model the real prompt: the system prompt built from the seed menu (Kaapi House) and the real output schema. It then scores the reply in code ([rules](rules.md#llm-evals)):

- `intent` must be one of the accepted intents.
- The cart must match exactly: the same dishes, quantities, and the expected keyword in each note.
- A sold-out dish is never added.
- An allergen question is identified, with the right allergen.
- A handoff is flagged when the customer asks for a person or complains, and only then.
- No amount appears in `reply` or `clarify`.

Load (100 customers at once, nothing lost or duplicated) is a separate test: `loadtest/`, with a stub model.

## Plan and status

| # | Step | Status |
|---|---|---|
| 1 | Eval-only client `evals/dashscope.py`. It uses the OpenAI-compatible API, the JSON schema when the model accepts it (otherwise JSON mode with the schema in the prompt), and drops a parameter a model rejects. It moves to the next model in `--chain` when one model's free quota is gone, and stops at `--token-cap` (600k) per model so a run can't reach the 1M quota | Done; 10 unit tests |
| 2 | One real call per model to check that it answers in our format | Done; 7 of 7 work |
| 3 | All 215 cases on each model, in parallel, with the app's own timeout (`LLM_TIMEOUT_SECONDS=10`) | Done |
| 4 | Gate metrics per model (`evals/gate.py`) and a review of every failure | Done (this page) |
| 5 | Prompt fixes for the real failures and a review of the disputed cases, then a re-run | **Team**: prompts are critical core (see [next steps](#next-steps)) |

Run it again:

```text
backend/.venv/Scripts/python.exe evals/run.py --provider dashscope --model qwen3.8-flash --delay 0.3
backend/.venv/Scripts/python.exe evals/run.py --provider dashscope --model qwen3.8-flash --chain qwen3.8-max-0902,deepseek-v4.1-flash-1017
backend/.venv/Scripts/python.exe evals/gate.py "20261025-*.json"
```

The `DASHSCOPE_API_KEY` goes in `backend/.env`. Full per-case reports are in `evals/reports/20261025-*.json`.

## Results

| Model | Overall | Cart exact | Hinglish | Handoff right | Allergen | p50 | p95 | Timeouts | Tokens used of 1M |
|---|---|---|---|---|---|---|---|---|---|
| **qwen3.8-max-0902** | **99.1 %** | **98.3 %** | 97.5 % | 100 % | 100 % | 2.2 s | 3.0 s | 0 | 337k |
| qwen3.8-27b | 97.7 % | 95.8 % | **100 %** | 100 % | 100 % | 2.0 s | 4.4 s | 0 | 447k |
| qwen3.8-flash | 97.2 % | 95.8 % | 95.0 % | 95.8 % | 100 % | 1.7 s | 2.9 s | 0 | 336k |
| deepseek-v4.1-flash-1017 | 96.3 % | 94.1 % | 90.0 % | 100 % | 100 % | **1.0 s** | **1.6 s** | 0 | 429k |
| deepseek-v4-flash-0731 | 95.8 % | 94.1 % | 92.5 % | 95.8 % | 100 % | 1.7 s | 2.1 s | 0 | 316k |
| kimi-k3 | 95.8 % | 94.9 % | 92.5 % | 100 % | 95.0 % | 2.5 s | 6.9 s | 5 | 394k |
| deepseek-v4.1-flash | 92.6 % | 92.4 % | 90.0 % | 95.8 % | 85.0 % | 2.3 s | 10.0 s | 12 | 404k |

- "Cart exact" covers the 118 cases with an expected cart.
- "Handoff right" covers the 24 cases that check it: 15 should hand off and 9 off-topic cases should not. Off-topic case `ot-2` checks only the intent.
- Latency is measured from this PC in India to Singapore. Production calls Vertex AI from Cloud Run, so these numbers compare the models with each other. They don't predict production latency.

### By category (passed / cases)

| Category | max-0902 | 27b | flash | v4.1-flash-1017 | v4-flash-0731 | kimi-k3 | v4.1-flash |
|---|---|---|---|---|---|---|---|
| Clean English | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 |
| Hinglish | 39/40 | 40/40 | 38/40 | 36/40 | 37/40 | 37/40 | 36/40 |
| Hindi script | 15/15 | 15/15 | 15/15 | 15/15 | 15/15 | 15/15 | 13/15 |
| Multi-item with notes | 20/20 | 19/20 | 20/20 | 18/20 | 17/20 | 18/20 | 18/20 |
| Ambiguous / misspelt | 24/25 | 21/25 | 22/25 | 23/25 | 23/25 | 24/25 | 24/25 |
| Recommendations | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 19/20 | 19/20 |
| Allergen | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 19/20 | 17/20 |
| Sold out | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| Handoff | 15/15 | 15/15 | 14/15 | 15/15 | 14/15 | 15/15 | 15/15 |
| Off-topic | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 9/10 |
| Edits and cancels | 15/15 | 15/15 | 15/15 | 15/15 | 15/15 | 14/15 | 13/15 |

### Against the Phase 2 gate

The gate requires:
- cart ≥ 95 % overall and ≥ 90 % on Hinglish;
- no invented items reaching the customer;
- every allergen question identified, so code can answer or hand off;
- handoff ≥ 98 %;
- p95 ≤ 3 s.

| Model | Cart | Hinglish | Invented items | Allergen | Handoff | p95 | Passes |
|---|---|---|---|---|---|---|---|
| qwen3.8-max-0902 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ 3.03 s, just over | All but latency, by 33 ms |
| qwen3.8-flash | ✅ | ✅ | ✅ | ✅ | ❌ 1 miss | ✅ | All but one handoff |
| qwen3.8-27b | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ 4.4 s | All but latency |
| deepseek-v4.1-flash-1017 | ❌ 94.1 % | ✅ | ✅ | ✅ | ✅ | ✅ | All but cart |
| deepseek-v4-flash-0731 | ❌ | ✅ | ✅ | ✅ | ❌ | ✅ | |
| kimi-k3 | ❌ | ✅ | ✅ | ❌ (a timeout) | ✅ | ❌ | |
| deepseek-v4.1-flash | ❌ | ✅ | ✅ | ❌ (timeouts) | ❌ | ❌ | |

**No model passes every line yet.** Two are one small step away: qwen3.8-max-0902 (33 ms over on latency) and qwen3.8-flash (one missed complaint). Most of the cart misses come from one prompt gap (class A below), so the prompt fixes could change this table.

**Invented items never reach a customer.** Some models put a word that isn't a dish into the cart ("decaf", "toast on the side"). The app's guardrail turns that into "not on our menu". A sold-out dish named by the customer is passed through as their own words, as the prompt asks, and code refuses it with alternatives. That's why sold out is 10/10 everywhere.

## What passed everywhere

- **Clean English (25/25 on all 7):** single and multiple dishes, quantities in words and digits.
- **Sold out (10/10 on all 7):** no model added Vietnamese Iced Coffee or Pesto Pasta, even when named exactly.
- **Allergen questions (20/20 on every model that answered in time):** the right allergen every time, including Hinglish ("butter chicken mein kaju hai?" → tree nuts) and sulphites.
- **Off-topic (10/10 on 6 models):** jokes, the IPL, weather and "are you a robot" got a short reply back to food, with no handoff.
- **Hindi script (15/15 on 6 models).**

## What failed and why

There were 55 failures across the 1,505 runs (7 models × 215 cases), in 8 classes. Classes A, D, E, F and G are real model or prompt problems. Classes B, C and H are disputed: the model's answer is reasonable and the case may expect too much. The team decides those.

### A. "One of them" changes: real, 14 failures, the biggest class

| Case | Message | Wrong on |
|---|---|---|
| v2-hin-30 | "teen cappuccino, ek mein sugar nahi" | 5 models |
| v2-mul-1 | "2 cold coffee, one without sugar, and a brownie warmed up" | 4 |
| v2-mul-9 | "two chicken club sandwiches, one without mustard" | 3 |
| v2-hin-6 | "2 bombay sandwich, ek mein cheese extra" | deepseek-v4-flash-0731 |
| mi-1 | "2 cappuccino one with extra foam, …" | kimi-k3 |

- **What the models did:** most added all N, then `set_note` "no sugar". In the cart `set_note` applies to the whole line, so **all three cappuccinos come without sugar**. The DeepSeek v4 models added N plain plus one more with the note, so the **customer gets and pays for 4**.
- **Correct:** two lines, `add 2` plain plus `add 1` with the note. The cart supports this, because lines are kept apart by note.
- **Why:** the prompt has no rule or example for "one of them". Only qwen3.8-max and, mostly, qwen3.8-flash and 27b got it right unaided.
- **Fix (prompt, team):** one rule and one few-shot: "N of a dish, one with a change → add N−1 without a note and 1 with the note".

### B. A dish name on its own: disputed, 11 failures

| Case | Message | Wrong on |
|---|---|---|
| v2-amb-4 | "cold cofee" | 4 models |
| v2-amb-11 | "risoto" | all three Qwen |
| v2-amb-16 | "omlet" | 1 model |
| v2-amb-13 | "club sandwich" | 1 model |
| v2-amb-14 | "green curry" | 1 model |
| v2-amb-3 | "a cafe late" | 1 model |

- **What the models did:** `browse` (or `unclear`) with an empty cart. They read a bare dish name as "tell me about it", not "add one".
- **Production effect:** nothing wrong reaches the cart. The customer gets the dish's details or a question, and one more message.
- **Decision needed:** is a bare dish name an order? If yes, add a prompt rule ("a dish name alone = add 1; the order summary confirms"). If no, accept `browse` in these cases.

### C. Notes kept in Hinglish: disputed, 4 failures

- **Case:** v2-hin-29 "ek cold coffee aur ek brownie, brownie garam karke" on 4 models.
- **What the models did:** wrote the note `"garam karke"`, the customer's own words. The case expects the keyword "warm".
- **Production effect:** the kitchen ticket says "garam karke". Staff understand it.
- **Decision needed:** should notes be written in English for the ticket? If yes, a prompt line. If no, the case accepts either word.

### D. "do" read as English, not "two": real, 3 failures

- **Case:** v2-hin-32 "do avocado toast". qwen3.8-flash and both DeepSeek flash models (0731, 1017) answered qty 1.
- **Why:** "do" at the start reads like the English verb ("do me an avocado toast"). The prompt already says `"do"=2`, but has no example where "do" starts the message.
- **Fix (prompt, team):** a few-shot that starts with "do".

### E. No answer within 10 s: real for these models, 17 failures

kimi-k3 had 5 timeouts and deepseek-v4.1-flash had 12, spread across categories. In production a timeout is retried, then handed to staff, so the customer waits. Both are also too slow overall (p95 6.9 s and 10 s). **Don't use them for live testing.** The dated `deepseek-v4.1-flash-1017` is the fast version of the same family (p95 1.6 s, no timeouts).

### F. A handoff missed: real, 2 failures, serious

| Case | Message | Model | Answered |
|---|---|---|---|
| v2-han-10 | "staff please" | deepseek-v4-flash-0731 | `smalltalk` |
| v2-han-4 | "this is wrong, my order is cold" | qwen3.8-flash | `modify_cart`, no handoff |

Invariant 11 says a person is one tap away. The router's keyword path catches some asks before the model, but only exact phrases such as "staff", "call staff" and "waiter". "staff please" isn't one, so in production it reaches the model too, as does any complaint like v2-han-4.

**Fix (team, critical core):**
- Prompt: add a complaint about a served order ("cold", "wrong", "late") to the handoff examples.
- Router keywords: add "staff please", or match "staff" / "waiter" anywhere in a short message.

### G. A wrong item: real, 2 failures

| Case | Message | Model | What it did |
|---|---|---|---|
| v2-hin-21 | "do flat white, ek decaf" | deepseek-v4.1-flash-1017 | Made "decaf" a separate dish, so 1 Flat White instead of 2 (one with the note "decaf"). The guardrail refuses "decaf" as not on the menu |
| v2-mul-13 | "masala omelette with extra green chilli and toast on the side" | deepseek-v4-flash-0731 | Added **Bombay Sandwich** for "toast". The order summary would show it before Confirm |

### H. "something nice": disputed, 2 failures

- **Case:** v2-amb-20, on deepseek-v4.1-flash-1017 and deepseek-v4-flash-0731.
- **What the models did:** answered `smalltalk`, a friendly line back to food. The case doesn't list `smalltalk` as acceptable.
- **Production effect:** harmless.
- **Decision needed:** whether to add `smalltalk` to the accepted intents.

### Real versus disputed, per model

If B, C and H are accepted as reasonable answers, the real failures per model are:

| Model | Real failures | Real accuracy |
|---|---|---|
| qwen3.8-max-0902 | 0 | 100 % |
| qwen3.8-27b | 1 (A) | 99.5 % |
| qwen3.8-flash | 3 (A, D, F) | 98.6 % |
| deepseek-v4.1-flash-1017 | 5 (A ×3, D, G) | 97.7 % |
| deepseek-v4-flash-0731 | 7 (A ×4, D, F, G) | 96.7 % |
| kimi-k3 | 8 (A ×3, E ×5) | 96.3 % |
| deepseek-v4.1-flash | 14 (A ×2, E ×12) | 93.5 % |

## Notes from running it

- **kimi-k3** rejects `temperature`. The client drops it for that model and remembers.
- **deepseek-v4.1-flash** and **-1017** don't accept a JSON schema. They run in JSON mode with the schema written into the prompt, and they still followed it.
- **Qwen models** accept the schema directly, with thinking off.
- **One ~257 s stall in every run, at about the same moment in all seven parallel runs.** This PC's network dropped, as it has before, and every stalled case still passed. These are left out of the latency figures, which they wouldn't change: one case per run sits above the p95.
- **Quota used:** 316k–447k tokens per model of the 1M each. **deepseek-v4-flash-0731 expires on 30 Oct** with about 680k left. No model reached Stop-on-Exhaust, so the fallback chain wasn't triggered for real; the unit tests cover it.
- **Older Gemini report:** the Gemini run in `evals/reports/20261024-1521-gemini-3.5-flash.json` (25 %) used the first 32 cases and an earlier runner. It isn't comparable with these runs.

## Recommendation for testing

- **For accuracy:** `qwen3.8-max-0902`. It had no real failures, and its latency is at the gate line.
- **For speed with good accuracy:** `qwen3.8-flash` (p95 2.9 s) or `deepseek-v4.1-flash-1017` (p95 1.6 s, but class A errors).
- **Chain for long test sessions:** `--model qwen3.8-max-0902 --chain qwen3.8-flash,qwen3.8-27b,deepseek-v4.1-flash-1017`.
- **Not for live testing:** kimi-k3 and deepseek-v4.1-flash, because of the timeouts.

## Next steps

1. **Team (critical core):**
   - `domain/ai_agent/prompt.py`: the rule and few-shot for class A, a few-shot starting with "do" for class D, and a complaint example for class F.
   - `domain/conversation/router.py`: "staff please" in the help keywords (class F).
2. **Team:** decide classes B, C and H, then update the expectations in `evals/cases/draft-v2.json`.
3. Re-run the four useful models after those changes. That's about 340k tokens each, which fits in the remaining quota.
4. Run the same 215 cases on Gemini (Vertex AI) once GCP is on. That run decides the production model; this one only shows what the prompt and cases need.
