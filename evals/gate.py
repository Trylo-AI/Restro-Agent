"""Phase 2 gate metrics (docs/rules.md#llm-evals) for eval reports: cart exact match, Hinglish, human intent,
allergens, add ops whose dish isn't on the menu at all, p95 latency. Turn metrics skip `purpose: recommend` cases.

Recommend reports (`run.py --purpose recommend`) get the recommend gate instead: 0 picks outside ALLOWED · 0 amounts
or claims reaching the customer · `any_of` >= 90 % · p95 <= 3 s. Exit 1 when a recommend report fails it.

    backend/.venv/Scripts/python.exe evals/gate.py ["20261025-*.json"]     # default: every report
"""

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("backend", "supabase/seed", "evals"):
    sys.path.insert(0, str(ROOT / p))

from run import CASES, REPORTS, recommend_gate, seed_menu  # noqa: E402

from app.domain.ai_agent import guardrails, prompt  # noqa: E402


def pct(xs: list[dict]) -> float:
    return round(100 * sum(1 for r in xs if r["pass"]) / max(len(xs), 1), 1)


def main() -> None:
    cases = {c["id"]: c for f in CASES.glob("*.json") for c in json.loads(f.read_text(encoding="utf-8"))}
    everything, available = seed_menu()
    view = prompt.build_view("Kaapi House", "warm", available)
    failed = False
    for f in sorted(glob.glob(str(REPORTS / (sys.argv[1] if len(sys.argv) > 1 else "*.json")))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if "model" not in d["summary"]:
            continue  # an app_run.py report (the whole system, no model output to gate)
        if d["summary"].get("purpose") == "recommend":
            ok, m = recommend_gate(d["results"])
            failed |= not ok
            print(f"{d['summary']['model']:<26} recommend {'PASS' if ok else 'FAIL'}  outside ALLOWED {m['outside_allowed']}  "
                  f"amounts/claims {m['unsafe_text']}  any_of {m['any_of_pct']:>5}%  p95 {m['p95_ms']:>5} ms")
            continue
        res = [r for r in d["results"]
               if r["pass"] is not None and cases.get(r["id"], {}).get("purpose", "turn") == "turn"]
        cart = [r for r in res if "cart" in cases[r["id"]]["expect"]]
        hing = [r for r in res if r["category"] == "hinglish"]
        human = [r for r in res if "human" in cases[r["id"]]["expect"]]
        allergen = [r for r in res if r["category"] == "allergen"]
        off_menu = []  # sold-out dishes by name are fine (code refuses them); these match nothing at all
        for r in res:
            for op in (r["output"] or {}).get("cart_ops") or []:
                ref = str(op.get("item_ref"))
                if op.get("op") == "add" and not view.by_short.get(ref) and not guardrails.match_by_name(ref, everything):
                    off_menu.append(f"{r['id']}:{ref}")
        lat = sorted(r["latency_ms"] for r in res) or [0]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"{d['summary']['model']:<26} overall {pct(res):>5}%  cart {pct(cart):>5}%  hinglish {pct(hing):>5}%  "
              f"human {pct(human):>5}%  allergen {pct(allergen):>5}%  p95 {p95:>5} ms  off-menu {off_menu}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
