"""Evaluator-side checks for evidence indexing, semantic errors and assignment tie-breaks."""
import json

import pytest

from judge_adapter import generate_episode, score_episode
from proofledger.env import Environment
from proofledger.matcher import evaluate, explain, public_evidence_ids, score_submission
from proofledger.schema import validate
from proofledger.trace import build


def issue(issue_id, *groups):
    return {"issue_id": issue_id, "type": "fx_error", "affected_ids": ["R1"],
            "expected_amount_minor": 10000, "currency": "KZT", "evidence_sets": list(groups)}


def finding(*evidence, **changes):
    return {"type": "fx_error", "affected_ids": ["R1"], "claimed_amount_minor": 10000,
            "currency": "KZT", "evidence_ids": list(evidence), "explanation": "", **changes}


def episode(issues):
    return {"manifest": {"episode_id": "E"}, "truth": {"issues": issues},
            "ledger": [{"row_id": "R1"}], "invoices": [{"doc_id": "D1"}, {"doc_id": "D2"}],
            "payments": [{"doc_id": "D3"}],
            "fx": [{"date": "2026-11-01", "source_currency": "USD", "target_currency": "KZT"}]}


def test_extra_evidence_must_be_accessible_m03_m04():
    data = episode([issue("private-issue", ["D1", "D2"])])
    available = public_evidence_ids(data)
    assert available == {"D1", "D2", "D3", "R1", "2026-11-01|USD|KZT"}
    fs = [finding("D2", "D1", "D3"), finding("D1", "D2", "private-issue")]
    score, error = score_submission(data, {"episode_id": "E", "findings": fs}, 10)
    assert error is None and (score["tp"], score["fp"], score["fn"]) == (1, 1, 0)
    verdicts = explain(data["truth"]["issues"], fs, available)["findings"]
    assert [row["verdict"] for row in verdicts] == ["TP", "FP"]
    assert "evidence" in verdicts[1]["reason"]


def test_unknown_string_values_only_disqualify_the_individual_finding_m06():
    data = episode([issue("A", ["D1"])])
    fs = [finding("D1", currency="kzt"), finding("D1", type="unknown"), finding("D1")]
    score, error = score_submission(data, {"episode_id": "E", "findings": fs}, 10)
    assert error is None and (score["tp"], score["fp"], score["fn"]) == (1, 2, 0)


def test_structural_error_has_null_metrics():
    data = episode([issue("A", ["D1"])])
    fs = [finding("D1", claimed_amount_minor=True)]
    score, error = score_submission(data, {"episode_id": "E", "findings": fs}, 10)
    assert error and score == {"tp": None, "fp": None, "fn": None, "precision": None,
                               "recall": None, "f1": None, "return": -1}


def test_non_greedy_maximum_m10():
    issues = [issue("B", ["D1"]), issue("A", ["D1"], ["D2"])]
    fs = [finding("D1"), finding("D2")]
    results = explain(issues, fs)["findings"]
    assert [row["issue_id"] for row in results] == ["B", "A"]
    assert evaluate(issues, fs) == evaluate(issues, fs[::-1])


def test_tie_uses_issue_order_and_original_finding_indices_m11():
    # All six edges exist. A and B are preferred to C. A must take the
    # original index zero, even with unsorted issue IDs and reverse evidence order.
    issues = [issue(key, ["D2"], ["D1"]) for key in ("C", "B", "A")]
    fs = [finding("D2"), finding("D1")]
    for order in (fs, fs[::-1]):
        results = explain(issues, order)
        assert [row["finding"] for row in results["findings"]] == order
        assert [row["issue_id"] for row in results["findings"]] == ["A", "B"]
        assert [row["issue_id"] for row in results["missed"]] == ["C"]


@pytest.mark.parametrize("public_tau,issue_tau,expected_tp", [
    (1, None, 1), (1, 0, 0), (None, None, 0),
])
def test_public_tolerance_in_step_score_and_explanation(tmp_path, public_tau, issue_tau, expected_tp):
    generate_episode(792154, {"fx_error": 1}, str(tmp_path))
    truth_path, policy_path = tmp_path / "truth.json", tmp_path / "policy.json"
    truth = json.loads(truth_path.read_text(encoding="utf8"))
    policy = json.loads(policy_path.read_text(encoding="utf8"))
    issue = truth["issues"][0]
    for record, key, value in [(issue, "tolerance_minor", issue_tau),
                               (policy, "amount_tolerance_minor", public_tau)]:
        if value is None:
            del record[key]
        else:
            record[key] = value
    assert validate(truth, "truth") == []
    truth_path.write_text(json.dumps(truth), encoding="utf8")
    policy_path.write_text(json.dumps(policy), encoding="utf8")

    env = Environment(mode="train")
    env.reset(0, {"episode_path": str(tmp_path)})
    answer = env._oracle_findings()
    answer["findings"][0]["claimed_amount_minor"] += 1
    _, reward, done, truncated, _ = env.step({"action": "submit", "findings": answer})
    score = score_episode(str(tmp_path), answer, 0)
    assert (done, truncated, score["tp"], reward) == (True, False, expected_tp, score["return"])
    assert build(env, "test")["verdicts"][0]["verdict"] == ("TP" if expected_tp else "FP")
    assert score == score_episode(str(tmp_path), answer, 0)
    assert json.loads(truth_path.read_text(encoding="utf8")) == truth
