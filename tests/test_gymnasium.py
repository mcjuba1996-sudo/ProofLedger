"""Gymnasium conventions (https://gymnasium.farama.org/api/env/)."""
import json

import pytest

from proofledger import Environment


def test_reset_keyword_and_positional_forms_match():
    a, b = Environment(), Environment()
    oa, ia = a.reset(seed=11, options={"level": "L3"})
    ob, ib = b.reset(11, {"level": "L3"})
    assert json.dumps(oa, sort_keys=True) == json.dumps(ob, sort_keys=True) and ia == ib


def test_seed_none_continues_a_reproducible_sequence():
    a, b = Environment(), Environment()
    a.reset(seed=3)
    b.reset(seed=3)
    assert a.reset()[1]["episode_id"] == b.reset()[1]["episode_id"]
    assert Environment().reset()[1]["episode_id"] != Environment().reset()[1]["episode_id"]


def test_fresh_objects_every_call():
    env = Environment()
    o1, i1 = env.reset(seed=1)
    o2, _, _, _, i2 = env.step({"action": "calculator", "expression": "1"})
    assert o1 is not o2 and i1 is not i2
    o3, *_ = env.step({"action": "calculator", "expression": "1"})
    assert o3 is not o2


def test_attributes_render_close():
    env = Environment(render_mode="ansi")
    assert env.metadata["render_modes"] == ["ansi"] and env.unwrapped is env and env.spec is None
    assert "oneOf" in env.action_space and "properties" in env.observation_space
    assert env.render() is None
    env.reset(seed=2)
    assert "step 0/100" in env.render()
    env.close()
    env.close()
    with pytest.raises(ValueError):
        Environment(render_mode="human")


def test_gymnasium_check_env():
    pytest.importorskip("gymnasium")
    from gymnasium.utils.env_checker import check_env
    from proofledger.gym_adapter import ProofLedgerGymEnv
    env = ProofLedgerGymEnv(level="L3", render_mode="ansi")
    check_env(env)
    obs, info = env.reset(seed=4)
    obs, r, te, tr, info = env.step('{"action": "submit", "findings": {"episode_id": "%s", "findings": []}}'
                                    % info["episode_id"])
    assert te and isinstance(r, float) and json.loads(obs)["result"]["submitted"]
