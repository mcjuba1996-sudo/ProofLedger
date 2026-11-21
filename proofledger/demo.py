"""Terminal demo of one episode, step by step.

python -m proofledger.demo --level L3 --seed 7 --policy reconciler
python -m proofledger.demo --level L3 --seed 7 --policy naive_reconciler
"""
import argparse
import sys

from . import trace
from .env import Environment
from .policies import make, run

COL = {"TP": "\033[32m", "FP": "\033[31m", "MISS": "\033[33m", "END": "\033[0m", "DIM": "\033[2m"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="L3")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--policy", default="reconciler")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)
    c = {k: "" for k in COL} if a.no_color or not sys.stdout.isatty() else COL
    env = Environment("train")
    run(make(a.policy, env), env, a.seed, {"level": a.level})
    t = trace.build(env, a.policy, a.level, a.seed)
    print(f"ProofLedger demo · level {a.level} · seed {a.seed} · policy {a.policy}")
    print(f"episode {t['episode_id']} · period {t['period']} · company tz {t['timezone']} · "
          f"{t['counts']['ledger_rows']} ledger rows, {t['counts']['invoices']} invoices, "
          f"{t['counts']['payments']} payments · hidden issues: {t['issues']}\n")
    for s in t["steps"]:
        print(f"{c['DIM']}{s['step']:>3}{c['END']}  {s['action']:<44} -> {s['obs']}")
    print("\nEvaluator verdicts (shown after the run, never during it):")
    for v in t["verdicts"]:
        tag = "TP" if v["verdict"] == "TP" else "FP"
        print(f"  {c[tag]}{tag}{c['END']}  {v['type']:<19} {','.join(v['affected_ids']):<26} "
              f"{v['amount']:>14} {v['currency']}  {'' if tag == 'TP' else v['reason']}")
    for m in t["missed"]:
        print(f"  {c['MISS']}MISS{c['END']} {m['type']:<19} {','.join(m['affected_ids'])}")
    r = t["report"]
    print(f"\nP={r['precision']:.2f} R={r['recall']:.2f} F1={r['f1']:.2f} "
          f"return={r['reward']:.3f} steps={r['steps']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
