"""One-command self-check: python -m proofledger.verify [--seeds N]

Runs the baseline policies on open seeds for every error family, prints diagnostics
(P, R, F1, return, steps, time) and checks the reward orderings required by the case,
plus paired checks (same seed/actions replay exactly; relabel keeps oracle and reconciler reward).
Exit code 0 only if every check passes.
"""
import argparse
import json
import sys
import time

from .policies import POLICIES, run_policy

CONFIGS = {
    "clean": {},
    "dup": {"duplicate_invoice": 2},
    "alloc": {"payment_allocation": 2},
    "fx": {"fx_error": 2},
    "period": {"period_error": 2},
    "mix_fx_alloc": {"mix_fx_allocation": 1},
    "mix_fx_period": {"mix_fx_period": 1},
    "all": {"duplicate_invoice": 1, "payment_allocation": 1, "fx_error": 1, "period_error": 1,
            "mix_fx_allocation": 1, "mix_fx_period": 1},
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    n = ap.parse_args(argv).seeds
    stats, worst_t = {}, 0.0
    for cname, cfg in CONFIGS.items():
        for pname in POLICIES:
            acc = {"precision": 0, "recall": 0, "f1": 0, "reward": 0, "steps": 0, "ms": 0}
            for seed in range(n):
                t0 = time.perf_counter()
                rep = run_policy(pname, seed, cfg)["report"]
                dt = time.perf_counter() - t0
                worst_t = max(worst_t, dt)
                for k in ("precision", "recall", "f1", "reward", "steps"):
                    acc[k] += rep[k]
                acc["ms"] += dt * 1000
            stats[(cname, pname)] = {k: v / n for k, v in acc.items()}
    print(f"{'config':14}{'policy':16}{'P':>6}{'R':>6}{'F1':>6}{'return':>9}{'steps':>7}{'ms':>7}")
    for (c, p), a in stats.items():
        print(f"{c:14}{p:16}{a['precision']:6.2f}{a['recall']:6.2f}{a['f1']:6.2f}"
              f"{a['reward']:9.3f}{a['steps']:7.1f}{a['ms']:7.1f}")

    R = lambda c, p: stats[(c, p)]["reward"]
    checks = []
    for c in CONFIGS:
        checks.append((f"oracle return = 1 on {c}", R(c, "oracle") == 1.0))
        checks.append((f"reconciler F1 = 1 on {c}", stats[(c, "reconciler")]["f1"] == 1.0))
    for c in CONFIGS:
        if c == "clean":
            continue
        checks.append((f"reconciler beats silence on {c}", R(c, "reconciler") > R(c, "silence")))
        checks.append((f"flag_all does not beat silence on {c}", R(c, "flag_all") <= R(c, "silence")))
    checks.append(("silence beats flag_all on clean", R("clean", "silence") > R("clean", "flag_all")))

    replay, relabel = True, True
    for seed in range(min(n, 10)):
        a, b = run_policy("reconciler", seed, CONFIGS["all"]), run_policy("reconciler", seed, CONFIGS["all"])
        replay &= json.dumps(a["trajectory"], sort_keys=True) == json.dumps(b["trajectory"], sort_keys=True)
        for p in ("oracle", "reconciler"):
            x = run_policy(p, seed, CONFIGS["all"])["report"]
            y = run_policy(p, seed, {**CONFIGS["all"], "relabel": seed + 1})["report"]
            relabel &= (x["reward"], x["tp"], x["fp"], x["fn"]) == (y["reward"], y["tp"], y["fp"], y["fn"])
    checks.append(("same seed + actions replay exactly", replay))
    checks.append(("relabel (new ids/order) keeps oracle and reconciler result", relabel))
    checks.append((f"slowest episode {worst_t:.2f}s < 60s", worst_t < 60))

    from .attacks import zoo_report
    from .levels import LEVEL_IDS, evaluate_levels
    lv = evaluate_levels(("reconciler", "naive_reconciler", "flag_all"), min(n, 10))
    checks.append(("reconciler passes curriculum L1-L5", all(lv[("reconciler", l)]["passed"] for l in LEVEL_IDS)))
    checks.append(("naive reconciler passes L1-L2 and fails from L3 (traps work)",
                   lv[("naive_reconciler", "L2")]["passed"] and not lv[("naive_reconciler", "L3")]["passed"]))
    checks.append(("flag_all passes no level", not any(lv[("flag_all", l)]["passed"] for l in LEVEL_IDS)))
    for r in zoo_report(min(n, 10))["rows"]:
        checks.append((f"attack {r['attack']} is defeated ({r['rule']})", r["ok"]))

    from .generator import generate_episode
    from .integrity import check_episode
    from .levels import level_config
    from .schema import validate
    eps = [generate_episode(s, level_config(l, s)) for l in LEVEL_IDS for s in range(min(n, 10))]
    checks.append((f"{len(eps)} episodes: clean books balance before injection, every issue traced",
                   all(not check_episode(e) for e in eps)))
    checks.append(("episodes within 200-2000 rows, 20-200 documents, 0-12 errors",
                   all(200 <= len(e["ledger"]) <= 2000 and 20 <= len(e["invoices"]) + len(e["payments"]) <= 200
                       and len(e["truth"]["issues"]) <= 12 for e in eps)))
    sch = all(not validate(e["manifest"], "manifest") and not validate(e["truth"], "truth")
              and not any(validate(r, "ledger_row") for r in e["ledger"])
              and not any(validate(r, "invoice") for r in e["invoices"])
              and not any(validate(r, "payment") for r in e["payments"]) for e in eps)
    checks.append(("all records match the published JSON schemas", sch))

    from .policies import Reconciler
    from .serve import RemoteEnvironment
    with RemoteEnvironment("closed") as env:
        obs, info = env.reset(1, {"level": "L3"})
        pol, rewards = Reconciler(), []
        pol.reset(obs, info)
        while True:
            obs, r, te, tr, _ = env.step(pol.act(obs))
            rewards.append(r)
            if te or tr:
                break
        isolated = set(rewards) == {0.0} and vars(env).keys() == {"_p"}
    checks.append(("evaluator runs in a separate process; closed mode leaks no reward", isolated))

    ok = True
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok &= passed
    print(f"{sum(p for _, p in checks)}/{len(checks)} checks passed")
    print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
