import json

import pytest

from proofledger import Environment
from proofledger.policies import run_policy

CFG = {"duplicate_invoice": 2, "fx_error": 2}
SEED = 918273645
SUBMIT = lambda env, f: env.step({"action": "submit", "findings": {
    "episode_id": env._ep["manifest"]["episode_id"], "findings": f}})


def fresh(mode="train", cfg=CFG, seed=SEED, **kw):
    env = Environment(mode, **kw)
    obs, info = env.reset(seed, cfg)
    return env, obs, info


def test_reset_contract():
    env, obs, info = fresh()
    assert {"task", "manifest", "policy", "steps_remaining"} <= set(obs)
    assert obs["steps_remaining"] == 100 and "episode_id" in info
    json.dumps([obs, info])  # JSON-compatible


def test_step_contract_and_json():
    env, _, _ = fresh()
    out = env.step({"action": "list_records", "table": "invoices"})
    assert len(out) == 5
    obs, r, term, trunc, info = out
    assert r == 0.0 and not term and not trunc and obs["steps_remaining"] == 99
    json.dumps(out)


def test_pagination_cap_and_stable_cursor():
    env, _, _ = fresh()
    seen, cur = [], None
    while True:
        obs = env.step({"action": "list_records", "table": "ledger", "cursor": cur, "limit": 1000})[0]
        assert len(obs["result"]["records"]) <= 50
        seen += [r["row_id"] for r in obs["result"]["records"]]
        cur = obs["result"]["next_cursor"]
        if cur is None:
            break
    assert seen == sorted(seen) and len(seen) == len(set(seen)) == env._ep["manifest"]["counts"]["ledger_rows"]
    env2, _, _ = fresh()
    again = env2.step({"action": "list_records", "table": "ledger", "cursor": "50", "limit": 5})[0]
    assert [r["row_id"] for r in again["result"]["records"]] == seen[50:55]


@pytest.mark.parametrize("a", [{"action": "get_record", "table": "invoices", "id": "NOPE"},
                               {"action": "get_document", "doc_id": "NOPE"},
                               {"action": "hack"}, "string", {"action": "list_records", "table": "x"},
                               {"action": "list_records", "table": "fx", "cursor": "abc"},
                               {"action": "calculator", "expression": "1/0"}])
def test_bad_input_bounded_error_and_costs_a_step(a):
    env, _, _ = fresh()
    obs, r, term, trunc, _ = env.step(a)
    assert obs["error"]["code"] and obs["result"] is None and obs["steps_remaining"] == 99
    assert (r, term, trunc) == (0.0, False, False)


def test_get_record_and_document():
    env, _, _ = fresh()
    inv = env._ep["invoices"][0]
    assert env.step({"action": "get_record", "table": "invoices", "id": inv["invoice_id"]})[0]["result"]["record"] == inv
    d = env.step({"action": "get_document", "doc_id": inv["doc_id"]})[0]["result"]
    assert d["doc_type"] == "invoice" and d["document"] == inv


def test_returned_records_are_copies():
    env, _, _ = fresh()
    obs = env.step({"action": "list_records", "table": "invoices"})[0]
    obs["result"]["records"][0]["total_minor"] = -1
    obs2 = env.step({"action": "list_records", "table": "invoices"})[0]
    assert obs2["result"]["records"][0]["total_minor"] != -1


def test_submit_terminates_and_state_frozen_after():
    env, _, _ = fresh()
    obs, r, term, trunc, _ = SUBMIT(env, [])
    assert term and not trunc
    n = len(env.trajectory)
    obs, r, term2, _, _ = env.step({"action": "calculator", "expression": "1"})
    assert obs["error"]["code"] == "episode_done" and term2 and len(env.trajectory) == n


def test_invalid_submission_is_minus_one():
    env, _, _ = fresh()
    obs, r, term, _, _ = env.step({"action": "submit", "findings": {"episode_id": "wrong", "findings": []}})
    assert r == -1.0 and term and env.final_report()["error"]


def test_step_limit_truncation():
    env, _, _ = fresh()
    for n in range(100):
        out = env.step({"action": "calculator", "expression": "1+1"})
    obs, r, term, trunc, _ = out
    assert trunc and not term and obs["steps_remaining"] == 0
    assert env.step({"action": "calculator", "expression": "1"})[0]["error"]["code"] == "episode_done"
    assert env.final_report()["f1"] == 0.0


def test_submit_on_step_100_is_allowed():
    env, _, _ = fresh()
    for _ in range(99):
        env.step({"action": "calculator", "expression": "1"})
    obs, r, term, trunc, _ = env.step({"action": "submit", "findings": env._oracle_findings()})
    assert term and not trunc and r == pytest.approx(1 - 0.002 * 99)


def test_time_limit():
    t = [0.0]
    env, _, _ = fresh(clock=lambda: t[0])
    t[0] = 61.0
    obs, r, term, trunc, _ = env.step({"action": "calculator", "expression": "1"})
    assert trunc and not term


def test_closed_mode_hides_reward_and_diagnostics():
    env, _, _ = fresh("closed")
    outs = [env.step({"action": "calculator", "expression": "1"}),
            env.step({"action": "submit", "findings": env._oracle_findings()})]
    for obs, r, term, trunc, info in outs:
        assert r == 0.0 and "terminal" not in info and "reward" not in json.dumps(obs)
    assert env.final_report()["reward"] > 0.9


def test_train_mode_returns_terminal_reward():
    env, _, _ = fresh()
    _, r, term, _, info = env.step({"action": "submit", "findings": env._oracle_findings()})
    assert term and r == 1.0 == info["terminal"]["reward"]


def test_reproducibility_same_actions_same_trajectory():
    a = run_policy("reconciler", SEED, CFG)
    b = run_policy("reconciler", SEED, CFG)
    assert json.dumps(a["trajectory"], sort_keys=True) == json.dumps(b["trajectory"], sort_keys=True)


def test_reset_isolates_state():
    env, _, _ = fresh()
    env.step({"action": "calculator", "expression": "1"})
    obs, _ = env.reset(SEED + 1, CFG)
    assert obs["steps_remaining"] == 100 and len(env.trajectory) == 1


def test_no_leak_in_trajectories():
    for name in ("reconciler", "random_explorer", "flag_all", "silence"):
        for mode in ("train", "closed"):
            res = run_policy(name, SEED, CFG, mode)
            blob = json.dumps(res["trajectory"])
            for bad in ("ISS-", "evidence_sets", "provenance", "truth", str(SEED), "expected_amount",
                        "invoice_copied", "wrong_rate", "wrong_date", "PLX-CANARY", "missing_allocation", "partial_allocation",
                        "booked_before_issue"):
                assert bad not in blob, (name, mode, bad)
    env, obs, info = fresh()
    assert "seed" not in json.dumps([obs, info]) and str(SEED) not in json.dumps([obs, info])
