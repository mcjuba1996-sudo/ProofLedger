"""Controlled comparison of two policies on held-out seeds (paired, with a bootstrap CI).

python -m proofledger.compare naive_reconciler reconciler --level L3 --seeds 40

Seeds start at --offset (default 700000), away from the open train/dev seeds. The verdict says
"B better" only if the 95% CI of the mean paired difference (B - A) excludes zero.
Use this before claiming any improvement of a trained agent.
"""
import argparse
import random
import sys

from .policies import run_policy


def compare(a, b, config, seeds=40, offset=700000, resamples=2000):
    ra, rb = [], []
    for s in range(offset, offset + seeds):
        ra.append(run_policy(a, s, config)["report"]["reward"])
        rb.append(run_policy(b, s, config)["report"]["reward"])
    diff = [y - x for x, y in zip(ra, rb)]
    rng = random.Random(f"bootstrap|{a}|{b}|{offset}|{seeds}")
    means = sorted(sum(rng.choice(diff) for _ in diff) / len(diff) for _ in range(resamples))
    lo, hi = means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]
    mean = sum(diff) / len(diff)
    verdict = "B better" if lo > 0 else "A better" if hi < 0 else "no significant difference"
    return {"a": a, "b": b, "mean_a": sum(ra) / seeds, "mean_b": sum(rb) / seeds,
            "mean_diff": mean, "ci95": (lo, hi), "verdict": verdict, "seeds": seeds, "offset": offset}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--level", default=None)
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--offset", type=int, default=700000)
    x = ap.parse_args(argv)
    r = compare(x.a, x.b, {"level": x.level} if x.level else {}, x.seeds, x.offset)
    print(f"A={r['a']}: mean return {r['mean_a']:.3f}")
    print(f"B={r['b']}: mean return {r['mean_b']:.3f}")
    print(f"B - A = {r['mean_diff']:.3f}, 95% CI [{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}] on "
          f"{r['seeds']} held-out seeds from {r['offset']}: {r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
