"""Measured failure analysis: python -m proofledger.report [out_dir]  (default: ./reports)

Writes failure_analysis.md and results.json: curriculum table, why the naive baseline fails
(false positives and misses by type and reason), and the reward-hacking zoo. Deterministic.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from . import trace
from .attacks import zoo_report
from .env import Environment
from .generator import VERSION
from .levels import LEVEL_IDS, LEVELS, evaluate_levels
from .policies import make, run

SEEDS = 20


def failure_breakdown(policy="naive_reconciler", seeds=SEEDS):
    out = {}
    for lv in LEVEL_IDS:
        fp, fn = Counter(), Counter()
        for s in range(seeds):
            env = Environment()
            run(make(policy, env), env, s, {"level": lv})
            t = trace.build(env, policy, lv, s)
            for v in t["verdicts"]:
                if v["verdict"] == "FP":
                    fp[f"{v['type']}: {v['reason']}"] += 1
            for m in t["missed"]:
                fn[m["type"]] += 1
        out[lv] = {"fp": dict(fp.most_common()), "fn": dict(fn.most_common())}
    return out


def main(argv=None):
    out = Path((argv or sys.argv[1:] or ["reports"])[0])
    out.mkdir(parents=True, exist_ok=True)
    pols = ("reconciler", "naive_reconciler", "random_explorer", "flag_all")
    lv = evaluate_levels(pols, SEEDS)
    br = failure_breakdown()
    zoo = zoo_report(10)
    L = ["# ProofLedger: measured failure analysis", "",
         f"Generator {VERSION}. {SEEDS} seeds per level (0..{SEEDS - 1}), train mode. "
         "Regenerate: `python -m proofledger.report`.", "",
         "## Curriculum", "",
         "Solved = F1 is exactly 1. A level is passed at ≥ 90% solved.", "",
         "| policy | " + " | ".join(f"{x['id']} {x['title']}" for x in LEVELS) + " |",
         "|---|" + "---|" * len(LEVELS)]
    for p in pols:
        cells = [f"{'✓' if lv[(p, l)]['passed'] else '✗'} {lv[(p, l)]['solved']:.0%} · {lv[(p, l)]['reward']:.2f}"
                 for l in LEVEL_IDS]
        L.append(f"| {p} | " + " | ".join(cells) + " |")
    L += ["", "Cell: passed · share solved · mean return.", "",
          "## Why the naive reconciler fails",
          "", "Same rules as the reconciler, but it reads dates as printed and ignores timezone offsets. "
          "It passes L1–L2 (one timezone) and fails from L3, where records are printed in several offsets "
          "and legitimate invoices sit on the period boundary.", ""]
    for l in LEVEL_IDS:
        b = br[l]
        if not b["fp"] and not b["fn"]:
            L.append(f"- **{l}**: no errors.")
            continue
        fp = "; ".join(f"{k} ×{v}" for k, v in b["fp"].items()) or "none"
        fn = "; ".join(f"{k} ×{v}" for k, v in b["fn"].items()) or "none"
        L.append(f"- **{l}**: false positives: {fp}. Missed: {fn}.")
    L += ["", "Mechanism: a document printed in another offset shows a different calendar date, so the "
          "naive check looks up the wrong day's FX rate (false fx_error, missed real ones) and puts "
          "boundary invoices in the wrong period (false period_error, missed real cutoff errors). "
          "Duplicate invoices can be traced to their issuer and printed invoice number independently of the timezone. "
          "The 30-finding limit can also push real findings out of a noisy report.", "",
          "## Reward-hacking zoo", "",
          f"10 seeds. Honest reconciler: {zoo['honest'][0]:.3f} with errors / {zoo['honest'][1]:.3f} clean. "
          f"Silence: {zoo['silence'][0]:.3f} / {zoo['silence'][1]:.3f}.", "",
          "| attack | return with errors | return clean | rule | result |", "|---|---|---|---|---|"]
    for r in zoo["rows"]:
        L.append(f"| {r['attack']} | {r['dirty']:.3f} | {r['clean']:.3f} | {r['rule']} | {'PASS' if r['ok'] else 'FAIL'} |")
    L += ["", "`text_only` keeps the correct fields and replaces every explanation with filler: the reward "
          "is identical, so the score does not depend on persuasive text. `injection_follower` obeys "
          "instructions planted in invoice text and loses; the environment's truth never changes because "
          "of such text.", "",
          "## Limits of this analysis", "",
          "- Baselines are scripted; numbers describe the environment, not any trained model.",
          "- The reconciler shares detection code with the generator's solvability check; independent "
          "recomputation lives in the tests.", ""]
    (out / "failure_analysis.md").write_text("\n".join(L), encoding="utf-8")
    res = {"version": VERSION,
           "levels": {f"{p}|{l}": v for (p, l), v in lv.items()},
           "naive_breakdown": br, "zoo": zoo}
    (out / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out / 'failure_analysis.md'} and {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
