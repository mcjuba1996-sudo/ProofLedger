import pytest

from proofledger import Environment
from proofledger.policies import run_policy

DIRTY = [{"duplicate_invoice": 2}, {"fx_error": 2}, {"duplicate_invoice": 2, "fx_error": 2}]
SEEDS = range(10)


def r(policy, seed, cfg):
    return run_policy(policy, seed, cfg)["report"]


@pytest.mark.parametrize("cfg", DIRTY)
def test_correct_answer_beats_silence_on_dirty_books(cfg):
    for s in SEEDS:
        assert r("oracle", s, cfg)["reward"] > r("silence", s, cfg)["reward"]
        assert r("reconciler", s, cfg)["reward"] > r("silence", s, cfg)["reward"]


def test_silence_beats_false_accusations_on_clean():
    for s in SEEDS:
        assert r("silence", s, {})["reward"] > r("flag_all", s, {})["reward"]


@pytest.mark.parametrize("cfg", DIRTY)
def test_flag_everything_is_bad(cfg):
    for s in SEEDS:
        rep = r("flag_all", s, cfg)
        assert rep["tp"] == 0 and rep["reward"] <= -0.99


def test_duplicated_findings_give_no_benefit():
    for s in SEEDS:
        env = Environment()
        env.reset(s, DIRTY[2])
        f = env._oracle_findings()
        base = Environment()
        base.reset(s, DIRTY[2])
        _, r1, *_ = base.step({"action": "submit", "findings": f})
        f["findings"] = f["findings"] * 2 if len(f["findings"]) * 2 <= 30 else f["findings"]
        _, r2, *_ = env.step({"action": "submit", "findings": f})
        assert r2 < r1


def test_step_economy_differs_only_slightly():
    cfg = DIRTY[2]
    for s in range(3):
        rewards = []
        for extra in (0, 10):
            env = Environment()
            env.reset(s, cfg)
            for _ in range(extra):
                env.step({"action": "calculator", "expression": "1"})
            _, rw, *_ = env.step({"action": "submit", "findings": env._oracle_findings()})
            rewards.append(rw)
        assert rewards[0] > rewards[1] and rewards[0] - rewards[1] == pytest.approx(0.02)


def test_guessed_ids_without_evidence_do_not_score():
    env = Environment()
    env.reset(1, DIRTY[0])
    f = env._oracle_findings()
    for x in f["findings"]:
        x["evidence_ids"] = []
    _, rw, *_ = env.step({"action": "submit", "findings": f})
    assert rw == -1.0


def test_wrong_currency_and_amount_do_not_score():
    env = Environment()
    env.reset(1, DIRTY[1])
    f = env._oracle_findings()
    f["findings"][0]["currency"] = "USD"
    f["findings"][1]["claimed_amount_minor"] += 10 ** 6
    _, rw, *_ = env.step({"action": "submit", "findings": f})
    assert rw == -1.0


def test_resubmission_is_ignored():
    env = Environment()
    env.reset(1, DIRTY[0])
    env.step({"action": "submit", "findings": {"episode_id": "bad", "findings": []}})
    env.step({"action": "submit", "findings": env._oracle_findings()})
    assert env.final_report()["reward"] == -1.0


def test_reconciler_solves_every_open_seed():
    for cfg in DIRTY + [{}]:
        for s in range(30):
            assert r("reconciler", s, cfg)["f1"] == 1.0, (cfg, s)
