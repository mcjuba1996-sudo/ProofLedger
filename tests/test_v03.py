"""Tests for v0.3: adapters interface, curriculum, attack zoo, injections, verdict explanations, tools."""
import json

import pytest

from proofledger import Environment, generate_episode
from proofledger.attacks import ATTACKS, CLEAN, DIRTY
from proofledger.generator import INJECTIONS
from proofledger.levels import LEVEL_IDS, expand, level_config
from proofledger.matcher import explain
from proofledger.policies import FlagAll, Oracle, RandomExplorer, Reconciler, run_policy


@pytest.mark.parametrize("make", [lambda e: RandomExplorer(0), lambda e: Reconciler(), lambda e: FlagAll(),
                                  lambda e: Oracle(e)])
def test_adapter_interface_plain_loop(make):
    """The four baseline adapters work with nothing but reset/act and the public env contract."""
    env = Environment("closed")
    obs, info = env.reset(42, {"level": "L2"})
    pol = make(env)
    pol.reset(obs, info)
    for _ in range(100):
        obs, r, term, trunc, _ = env.step(json.loads(json.dumps(pol.act(obs))))
        assert r == 0.0
        if term or trunc:
            break
    assert term or trunc


def test_level_presets_are_deterministic_and_valid():
    for lv in LEVEL_IDS:
        for s in range(10):
            assert level_config(lv, s) == level_config(lv, s)
            generate_episode(s, level_config(lv, s))
    assert expand({"level": "L1", "n_invoices": 30}, 1)["n_invoices"] == 30
    with pytest.raises(ValueError):
        level_config("L9", 0)


def test_levels_get_harder():
    sizes = [sum(len(generate_episode(s, level_config(lv, s))["truth"]["issues"]) for s in range(10)) for lv in LEVEL_IDS]
    assert sizes[0] < sizes[1] and sizes[-1] == max(sizes)
    assert not level_config("L1", 0)["mixed_timezones"] and "mixed_timezones" not in level_config("L3", 0)


def test_curriculum_separates_naive_from_careful():
    def solved(p, lv):
        return sum(run_policy(p, s, {"level": lv})["report"]["f1"] == 1.0 for s in range(8))
    for lv in LEVEL_IDS:
        assert solved("reconciler", lv) == 8
    assert solved("naive_reconciler", "L1") == 8
    assert solved("naive_reconciler", "L3") <= 2


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attack_is_defeated(name):
    def mean(p, cfg):
        return sum(run_policy(p, s, cfg)["report"]["reward"] for s in range(6)) / 6
    d, c = mean(name, DIRTY), mean(name, CLEAN)
    hd, hc, sc = mean("reconciler", DIRTY), mean("reconciler", CLEAN), mean("silence", CLEAN)
    if name == "text_only":
        assert (d, c) == (hd, hc)
    else:
        assert d < hd and c <= sc


def test_injections_appear_and_never_change_truth():
    seen = 0
    for s in range(10):
        ep = generate_episode(s, {"injection_rate": 0.5, "fx_error": 1})
        texts = [x["description"] for i in ep["invoices"] for x in i["line_items"]]
        seen += sum(t in INJECTIONS for t in texts)
        assert len(ep["truth"]["issues"]) == 1
        assert run_policy("reconciler", s, {"injection_rate": 0.5, "fx_error": 1})["report"]["f1"] == 1.0
    assert seen > 20


def test_explain_reasons():
    issue = {"issue_id": "ISS-1", "type": "fx_error", "affected_ids": ["INV-1"], "expected_amount_minor": 5000,
             "currency": "KZT", "tolerance_minor": 100, "evidence_sets": [["A-1", "L1"]]}
    good = {"type": "fx_error", "affected_ids": ["INV-1"], "claimed_amount_minor": 5000, "currency": "KZT",
            "evidence_ids": ["A-1", "L1"], "explanation": ""}
    cases = [(dict(good), "matched"), (dict(good, affected_ids=["INV-2"]), "false accusation"),
             (dict(good, type="period_error"), "wrong type"), (dict(good, claimed_amount_minor=1), "amount"),
             (dict(good, currency="USD"), "currency"), (dict(good, evidence_ids=["A-1"]), "evidence")]
    for f, word in cases:
        r = explain([issue], [f])
        assert word in r["findings"][0]["reason"]
    r = explain([issue], [good, good])
    assert [x["verdict"] for x in r["findings"]] == ["TP", "FP"] and "repeat" in r["findings"][1]["reason"]
    assert explain([issue], [])["missed"][0]["issue_id"] == "ISS-1"


def test_compare_protocol():
    from proofledger.compare import compare
    r = compare("naive_reconciler", "reconciler", {"level": "L3"}, seeds=12)
    assert r["verdict"] == "B better" and r["ci95"][0] > 0
    r = compare("reconciler", "text_only", {"level": "L2"}, seeds=8)
    assert r["verdict"] == "no significant difference"


def test_tools_run(tmp_path, capsys):
    from proofledger import demo, report, viewer
    assert demo.main(["--level", "L3", "--seed", "1", "--no-color"]) == 0
    assert "Evaluator verdicts" in capsys.readouterr().out
    assert report.main([str(tmp_path / "r")]) == 0
    assert "Reward-hacking zoo" in (tmp_path / "r" / "failure_analysis.md").read_text(encoding="utf-8")
    html = (tmp_path / "v.html")
    viewer.build(str(html), seeds=3)
    text = html.read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text  # fully offline
    assert "PLX-CANARY" not in text
