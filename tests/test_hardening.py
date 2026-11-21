"""Regressions for malformed JSON, budgets and deterministic decimal arithmetic."""
import io
import json
from decimal import Inexact, ROUND_DOWN, localcontext

import pytest

from proofledger import Environment, generate_episode
from proofledger.calculator import calculate
from proofledger.generator import _check_config
from proofledger.serve import serve


@pytest.mark.parametrize("table", [[], {}, None, 12, True])
@pytest.mark.parametrize("action", ["list_records", "get_record"])
def test_malformed_table_is_a_logged_bounded_error(table, action):
    env = Environment()
    env.reset(1)
    obs, reward, terminated, truncated, info = env.step({"action": action, "table": table})
    assert obs["error"] == {"code": "unknown_table"}
    assert obs["steps_remaining"] == 99
    assert (reward, terminated, truncated) == (0.0, False, False)
    assert info["step"] == 1 and len(env.trajectory) == 2
    assert env.trajectory[-1]["observation"] == obs


@pytest.mark.parametrize("currency", [[], {}, None, 12, True])
def test_malformed_submission_currency_finishes_with_penalty(currency):
    env = Environment()
    env.reset(1, {"fx_error": 1})
    answer = env._oracle_findings()
    answer["findings"][0]["currency"] = currency
    obs, reward, terminated, truncated, _ = env.step({"action": "submit", "findings": answer})
    assert (reward, terminated, truncated) == (-1.0, True, False)
    assert obs["error"]["code"] == "invalid_submission"
    assert env.final_report()["error"] == "findings[0].currency invalid"
    assert len(env.trajectory) == 2


def test_malformed_actions_cannot_bypass_budget():
    env = Environment()
    env.reset(1, {"fx_error": 1})
    for _ in range(100):
        obs, _, terminated, truncated, _ = env.step({"action": "list_records", "table": []})
    assert truncated and not terminated and obs["steps_remaining"] == 0
    assert env.final_report()["steps"] == 100
    assert len(env.trajectory) == 101
    report = env.final_report()
    obs, _, terminated, truncated, _ = env.step({"action": "submit", "findings": env._oracle_findings()})
    assert truncated and not terminated
    assert obs["error"] == {"code": "episode_done"}
    assert env.final_report() == report


def test_exhausted_budget_is_checked_before_submit_dispatch():
    env = Environment()
    env.reset(1, {"fx_error": 1})
    # Simulate an interrupted prior step: the independent entry guard must
    # still prevent a 101st action, even when the done flag was not set.
    env._steps = 100
    obs, reward, terminated, truncated, _ = env.step({"action": "submit", "findings": env._oracle_findings()})
    assert (reward, terminated, truncated) == (-1.0, False, True)
    assert obs["steps_remaining"] == 0 and env.final_report()["steps"] == 100


def test_submit_is_still_allowed_as_the_hundredth_action():
    env = Environment()
    env.reset(1, {"fx_error": 1})
    for _ in range(99):
        env.step({"action": "calculator", "expression": "1"})
    obs, reward, terminated, truncated, _ = env.step({"action": "submit", "findings": env._oracle_findings()})
    assert (reward, terminated, truncated) == (0.802, True, False)
    assert obs["steps_remaining"] == 0


def test_rpc_rejects_nonobjects_and_continues():
    inp = io.StringIO('[]\nnull\n"text"\n42\n{"op":"reset","seed":1}\n{"op":"close"}\n')
    out = io.StringIO()
    serve(inp=inp, out=out)
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(rows) == 6
    assert all(row["ok"] is False and row["error"] == "bad_request" for row in rows[:4])
    assert rows[4]["ok"] and rows[4]["observation"]["steps_remaining"] == 100
    assert rows[5] == {"ok": True}


def test_calculator_preserves_large_integers_and_caller_context():
    assert calculate("123456789012345*100000000000001") == "12345678901234623456789012345"
    assert calculate("123456789012345678901234567890") == "123456789012345678901234567890"
    thirds = calculate("1/3")
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert calculate("1000001+1") == "1000002"
        assert calculate("1/3") == thirds
        assert calculate("1/3*3") == "1"
        assert ctx.prec == 6 and ctx.rounding == ROUND_DOWN and ctx.traps[Inexact]


def test_entity_count_rejected_before_generation_can_loop():
    with pytest.raises(ValueError, match="n_entities must be <= 840"):
        generate_episode(0, {"n_entities": 841})
    assert _check_config({"n_entities": 840})["n_entities"] == 840


def test_split_payment_residual_rounding_is_public():
    env = Environment()
    observation, _ = env.reset(1)
    rule = observation["policy"]["split_payment_rounding"]
    assert "remaining converted total" in rule
    assert "one minor unit" in rule
