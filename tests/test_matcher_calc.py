import pytest

from proofledger.calculator import CalcError, calculate
from proofledger.matcher import evaluate, terminal_reward, validate_submission

ISSUE = {"issue_id": "ISS-1", "type": "duplicate_invoice", "affected_ids": ["A", "B"],
         "expected_amount_minor": 1000, "currency": "USD", "tolerance_minor": 100,
         "evidence_sets": [["D1", "D2"]]}
AVAILABLE = {"D1", "D2", "X"}


def F(**kw):
    f = {"type": "duplicate_invoice", "affected_ids": ["B", "A"], "claimed_amount_minor": 1050,
         "currency": "USD", "evidence_ids": ["D2", "D1", "X"], "explanation": ""}
    f.update(kw)
    return f


def test_match_canonical_ids_tolerance_superset_evidence():
    assert evaluate([ISSUE], [F()], AVAILABLE)["tp"] == 1


@pytest.mark.parametrize("kw", [{"type": "fx_error"}, {"currency": "EUR"}, {"affected_ids": ["A"]},
                                {"claimed_amount_minor": 1101}, {"evidence_ids": ["D1"]}])
def test_no_match(kw):
    r = evaluate([ISSUE], [F(**kw)], AVAILABLE)
    assert (r["tp"], r["fp"], r["fn"]) == (0, 1, 1)


def test_repeats_are_false_positives():
    r = evaluate([ISSUE], [F(), F(), F()], AVAILABLE)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 2, 0)
    assert r["f1"] < 1


def test_order_independent():
    a, b = F(), F(affected_ids=["Z"])
    assert evaluate([ISSUE], [a, b], AVAILABLE) == evaluate([ISSUE], [b, a], AVAILABLE)


def test_empty_cases():
    assert evaluate([], [])["f1"] == 1.0
    assert evaluate([], [F()])["f1"] == 0.0
    r = evaluate([ISSUE], [])
    assert r["f1"] == 0.0 and r["precision"] == 0.0 and r["recall"] == 0.0


def test_reward_formula_and_clip():
    assert terminal_reward(1.0, 0) == 1.0
    assert terminal_reward(0.0, 0) == -1.0
    assert terminal_reward(1.0, 10) == pytest.approx(0.98)
    assert terminal_reward(0.0, 100) == -1.0
    assert terminal_reward(1.0, 1) - terminal_reward(1.0, 2) == pytest.approx(0.002)


def test_validation():
    ok = {"episode_id": "E", "findings": [F()]}
    assert validate_submission(ok, "E") is None
    assert validate_submission(ok, "other")
    assert validate_submission([], "E")
    assert validate_submission({"episode_id": "E", "findings": [F()] * 31}, "E")
    for kw in ({"type": ["x"]}, {"affected_ids": []}, {"claimed_amount_minor": 1.5},
               {"claimed_amount_minor": True}, {"currency": 1}, {"evidence_ids": ["a"] * 11},
               {"explanation": "x" * 1001}):
        assert validate_submission({"episode_id": "E", "findings": [F(**kw)]}, "E")


@pytest.mark.parametrize("expr,val", [("1+2*3", "7"), ("(1+2)*3", "9"), ("10/4", "2.5"),
                                      ("0.1+0.2", "0.3"), ("-5+2", "-3"), ("1/3*3", "1")])
def test_calc_ok(expr, val):
    assert calculate(expr) == val


@pytest.mark.parametrize("expr,code", [("__import__('os')", "invalid_expression"), ("1/0", "division_by_zero"),
                                       ("", "invalid_expression"), ("2**3", "invalid_expression"),
                                       ("(" * 100 + "1" + ")" * 100, "invalid_expression"),
                                       ("9" * 150 + "*" + "9" * 40, "overflow"), ("1+", "invalid_expression"),
                                       ("1" * 201, "invalid_expression"), ("open('x')", "invalid_expression")])
def test_calc_rejects(expr, code):
    with pytest.raises(CalcError) as e:
        calculate(expr)
    assert e.value.code == code
