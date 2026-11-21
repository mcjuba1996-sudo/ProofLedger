"""Diagnostic run of four trusted baselines through the published judge API.

Run with python -m proofledger.judge_benchmark. Each policy sees the same 20
publicly reproducible episodes. This in-process timing benchmark uses bundled
policies only; a separate integration test covers the restricted policy worker.
"""
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from time import perf_counter

from judge_adapter import generate_episode, make_env, score_episode
from .env import Environment
from .policies import FlagAll, Oracle, RandomExplorer, Reconciler


SEED_BASE = 16100
EXPLORER_SEED = 709
# D01–05 clean, D06–15 single types, D16–20 the two documented interactions.
SCENARIOS = [
    {}, {"unpaid_rate": 0, "split_payment_rate": 1, "refund_rate": 0},
    {"refund_rate": 1, "unpaid_rate": 0}, {"recurring_entities": 4},
    {"boundary_distractors": 10}, *[{"duplicate_invoice": 1}] * 3,
    *[{"payment_allocation": 1}] * 3, *[{"fx_error": 1}] * 2,
    *[{"period_error": 1}] * 2, *[{"mix_fx_allocation": 1}] * 3,
    *[{"mix_fx_period": 1}] * 2,
]


def run_public_baseline(policy, folder, seed):
    """Drive official reset/step, score the submitted answer, count submit as a step."""
    env = make_env()
    started = perf_counter()
    obs, info = env.reset(seed, {"mode": "closed", "episode_dir": str(folder), "team_config": {}})
    policy.reset(obs, info)
    for steps in range(1, 101):
        # Installed baselines read the public reset fields and list_records pages directly.
        native = policy.act(obs)
        name = native["action"]
        args = {k: v for k, v in native.items() if k != "action"}
        obs, reward, done, truncated, _ = env.step({"name": name, "args": args})
        if reward != 0 or truncated:
            raise RuntimeError(f"{policy.name}: unexpected closed reward or truncation")
        if done:
            elapsed_ms = (perf_counter() - started) * 1000
            if name != "submit" or obs["result"] != {"accepted": True}:
                raise RuntimeError(f"{policy.name}: episode ended without an accepted submission")
            score = score_episode(str(folder), args["findings"], steps - 1)
            return {**score, "steps": steps, "elapsed_ms": elapsed_ms}
    raise RuntimeError(f"{policy.name}: episode exceeded its step budget")


def main():
    reports = {}
    with TemporaryDirectory(prefix="proofledger-benchmark-") as temp:
        for n, config in enumerate(SCENARIOS, start=1):
            seed, folder = SEED_BASE + n, Path(temp) / f"D{n:02d}"
            generate_episode(seed, config, str(folder))
            # The trusted judge supplies the original Oracle class its private reference
            # from this exact folder. Every execution, including Oracle's submit, is
            # driven and scored through the same public judge entry points above.
            oracle_reference = Environment(mode="closed")
            oracle_reference.reset(seed, {"episode_path": str(folder)})
            for policy in (RandomExplorer(EXPLORER_SEED), Reconciler(), FlagAll(), Oracle(oracle_reference)):
                report = run_public_baseline(policy, folder, seed)
                reports.setdefault(policy.name, []).append(report)

    print(f"Public D01-D20, seeds {SEED_BASE+1}-{SEED_BASE+len(SCENARIOS)}, explorer seed {EXPLORER_SEED}.")
    print("Per-episode means. Steps include submit; time is reset through terminal step (ms).")
    print(f"{'policy':<17}{'episodes':>8}{'P':>8}{'R':>8}{'F1':>8}{'return':>10}{'steps':>9}{'ms':>10}")
    for name, rows in reports.items():
        print(f"{name:<17}{len(rows):>8}", end="")
        for key, width in (("precision", 8), ("recall", 8), ("f1", 8), ("return", 10),
                           ("steps", 9), ("elapsed_ms", 10)):
            print(f"{fmean(row[key] for row in rows):>{width}.3f}", end="")
        print()


if __name__ == "__main__":
    main()
