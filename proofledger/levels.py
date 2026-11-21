"""Curriculum levels L1-L5: preset config families of growing difficulty.

Use through the normal contract: env.reset(seed, {"level": "L3"}) (extra keys override).
A level counts as solved by a policy when F1 = 1 on at least 90% of its seeds.
python -m proofledger.levels [--seeds N]
"""
import argparse
import random
import sys

LEVELS = [
    {"id": "L1", "title": "Первая ошибка", "goal": "Одна ошибка любого типа или чистые книги. Все даты в часовом поясе компании."},
    {"id": "L2", "title": "Все типы", "goal": "2–4 типа ошибок сразу, по 1–2 каждого. Без ловушек."},
    {"id": "L3", "title": "Ловушки", "goal": "Часовые пояса на границе месяца, возвраты, разбитые платежи, инъекции в тексте."},
    {"id": "L4", "title": "Сплетённые ошибки", "goal": "Валютный счёт + частичный платёж; счёт следующего месяца по курсу не той даты."},
    {"id": "L5", "title": "Годовой аудит", "goal": "До 20 контрагентов, 8–12 ошибок сразу, все ловушки, новые ID и порядок записей."},
]
LEVEL_IDS = [lv["id"] for lv in LEVELS]
SINGLES = ("duplicate_invoice", "payment_allocation", "fx_error", "period_error")
SOLVED_SHARE = 0.9


def level_config(level_id, seed):
    if level_id not in LEVEL_IDS:
        raise ValueError(f"unknown level: {level_id}")
    r = random.Random(f"level|{level_id}|{seed}")
    calm = {"boundary_distractors": 0, "refund_rate": 0.0, "injection_rate": 0.0,
            "split_payment_rate": 0.1, "recurring_entities": 1, "mixed_timezones": False}
    traps = {"boundary_distractors": r.randint(4, 8), "refund_rate": 0.2, "injection_rate": 0.1,
             "split_payment_rate": 0.3, "recurring_entities": 3}
    if level_id == "L1":
        cfg = dict(calm)
        if r.random() >= 0.2:
            cfg[r.choice(SINGLES)] = 1
    elif level_id == "L2":
        cfg = dict(calm)
        for t in r.sample(SINGLES, r.randint(2, 4)):
            cfg[t] = r.randint(1, 2)
    elif level_id == "L3":
        cfg = dict(traps)
        if r.random() >= 0.15:
            for t in r.sample(SINGLES, r.randint(1, 3)):
                cfg[t] = 1
    elif level_id == "L4":
        cfg = dict(traps)
        a = r.randint(0, 2)
        cfg.update(mix_fx_allocation=a, mix_fx_period=r.randint(1 if a == 0 else 0, 2))
        cfg[r.choice(SINGLES)] = 1
    else:
        cfg = dict(traps, n_entities=r.randint(12, 20), n_invoices=r.randint(55, 65),
                   relabel=r.randint(1, 10 ** 6), mix_fx_allocation=1, mix_fx_period=1)
        for _ in range(r.randint(4, 8)):
            t = r.choice(SINGLES)
            cfg[t] = cfg.get(t, 0) + 1
    return cfg


def expand(config, seed):
    """Resolve {"level": "L3", ...} into a generator config; other keys override the preset."""
    if not config or "level" not in config:
        return config
    extra = {k: v for k, v in config.items() if k != "level"}
    return {**level_config(config["level"], seed), **extra}


def evaluate_levels(policies=("reconciler", "naive_reconciler", "random_explorer", "flag_all"), seeds=20):
    from .policies import run_policy
    out = {}
    for p in policies:
        for lv in LEVEL_IDS:
            reps = [run_policy(p, s, {"level": lv})["report"] for s in range(seeds)]
            solved = sum(r["f1"] == 1.0 for r in reps) / seeds
            out[(p, lv)] = {"solved": solved, "passed": solved >= SOLVED_SHARE,
                            "f1": sum(r["f1"] for r in reps) / seeds,
                            "reward": sum(r["reward"] for r in reps) / seeds,
                            "fp": sum(r["fp"] for r in reps), "fn": sum(r["fn"] for r in reps),
                            "steps": sum(r["steps"] for r in reps) / seeds}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    res = evaluate_levels(seeds=ap.parse_args(argv).seeds)
    pols = sorted({p for p, _ in res}, key=lambda p: list(dict.fromkeys(q for q, _ in res)).index(p))
    print(f"{'policy':18}" + "".join(f"{lv:>16}" for lv in LEVEL_IDS) + "   (solved share / mean return)")
    for p in pols:
        cells = []
        for lv in LEVEL_IDS:
            x = res[(p, lv)]
            cells.append(f"{('✓' if x['passed'] else '✗')} {x['solved']:4.0%} {x['reward']:6.2f}")
        print(f"{p:18}" + "".join(f"{c:>16}" for c in cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
