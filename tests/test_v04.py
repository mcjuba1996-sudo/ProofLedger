"""v0.4: out-of-process evaluator, schemas, pre-injection integrity, file transforms, sizes, names."""
import json

import pytest

from proofledger import Environment, generate_episode
from proofledger.configs import DEV, TRAIN
from proofledger.generator import read_episode, write_episode
from proofledger.integrity import check_books, check_episode, restore_clean
from proofledger.levels import LEVEL_IDS, level_config
from proofledger.policies import FlagAll, Oracle, Reconciler, make, run, run_policy
from proofledger.schema import validate
from proofledger.serve import RemoteEnvironment
from proofledger.transform import transform


def drive(env, pol, seed, cfg):
    obs, info = env.reset(seed, cfg)
    pol.reset(obs, info)
    rewards = []
    while True:
        obs, r, te, tr, _ = env.step(pol.act(obs))
        rewards.append(r)
        if te or tr:
            return rewards


def test_remote_evaluator_isolated(tmp_path):
    log = tmp_path / "log.jsonl"
    with RemoteEnvironment("closed", log=log) as env:
        assert vars(env).keys() == {"_p"}  # the agent side holds only a pipe
        assert set(drive(env, Reconciler(), 3, {"level": "L4"})) == {0.0}
        assert set(drive(env, FlagAll(), 3, {"level": "L4"})) == {0.0}
        with pytest.raises(RuntimeError):
            env._oracle_findings()
        with pytest.raises(RuntimeError):
            env._call({"op": "eval", "code": "open('truth.json')"})
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert [r["f1"] for r in rows] == [1.0, 0.0]
    local = run_policy("reconciler", 3, {"level": "L4"})["report"]
    assert rows[0]["reward"] == local["reward"]


def test_remote_oracle_only_when_allowed():
    with RemoteEnvironment("train", allow_oracle=True) as env:
        assert drive(env, Oracle(env), 1, {"level": "L5"})[-1] == 1.0


@pytest.mark.parametrize("lv", LEVEL_IDS)
def test_clean_books_before_injection(lv):
    for s in range(12):
        ep = generate_episode(s, level_config(lv, s))
        assert check_episode(ep) == [], (lv, s)


def test_injected_books_violate_rules_exactly_where_declared():
    ep = generate_episode(4, {"fx_error": 1})
    bad = check_books(ep)
    iid = ep["truth"]["issues"][0]["affected_ids"][0]
    assert len(bad) == 1 and iid in bad[0]
    clean = generate_episode(4, {})
    assert check_books(clean) == [] and restore_clean(clean)["ledger"] == clean["ledger"]


def test_schemas_accept_data_and_reject_garbage():
    ep = generate_episode(2, level_config("L4", 2))
    assert not validate(ep["manifest"], "manifest") and not validate(ep["truth"], "truth")
    for name, rows in (("ledger_row", ep["ledger"]), ("invoice", ep["invoices"]),
                       ("payment", ep["payments"]), ("fx_row", ep["fx"])):
        assert not [e for r in rows for e in validate(r, name)]
    for t in run_policy("reconciler", 2, {"level": "L4"})["trajectory"]:
        assert not validate(t, "trajectory_row") and not validate(t["observation"], "observation")
    assert validate({"action": "shell", "cmd": "ls"}, "action")
    assert validate({"episode_id": "x", "findings": [{"type": "fx_error"}]}, "findings")
    assert validate({"result": None, "error": None, "steps_remaining": 100, "truth": {}}, "observation")


def test_load_from_files_and_harmless_transform(tmp_path):
    for s in range(4):
        ep = generate_episode(s, level_config("L5", s))
        write_episode(ep, tmp_path / f"a{s}", include_private=True)
        assert read_episode(tmp_path / f"a{s}") == {k: ep[k] for k in read_episode(tmp_path / f"a{s}")}
        mapping = transform(tmp_path / f"a{s}", tmp_path / f"b{s}", seed=s)
        assert len(mapping) > 100
        for p in ("oracle", "reconciler", "flag_all"):
            reps = []
            for d in (f"a{s}", f"b{s}"):
                env = Environment("closed")
                r = run(make(p, env), env, 0, {"episode_path": str(tmp_path / d)})
                reps.append((r["reward"], r["tp"], r["fp"], r["fn"]))
            assert reps[0] == reps[1], (s, p)
        assert check_episode(read_episode(tmp_path / f"b{s}")) == []


def test_episode_path_requires_truth(tmp_path):
    write_episode(generate_episode(0), tmp_path / "open")
    with pytest.raises(ValueError):
        Environment().reset(0, {"episode_path": str(tmp_path / "open")})


def test_sizes_within_suggested_ranges():
    eps = [generate_episode(s, level_config(lv, s)) for lv in LEVEL_IDS for s in range(25)]
    eps += [generate_episode(s, c) for s, c in TRAIN + DEV]
    for ep in eps:
        assert 200 <= len(ep["ledger"]) <= 2000
        assert 20 <= len(ep["invoices"]) + len(ep["payments"]) <= 200
        assert 0 <= len(ep["truth"]["issues"]) <= 12


def test_names_vary_and_are_consistent():
    ep = generate_episode(5)
    by_ent = {}
    for i in ep["invoices"]:
        by_ent.setdefault(i["entity_id"], set()).add(i["entity_name"])
    assert all(len(v) == 1 for v in by_ent.values())
    assert len({next(iter(v)) for v in by_ent.values()}) == len(by_ent)
    assert {i["entity_name"] for i in ep["invoices"]} != {i["entity_name"] for i in generate_episode(6)["invoices"]}
