"""Public GENESIS examples through the three competition entry points."""
import csv
import json
from decimal import Decimal

import pytest

from judge_adapter import generate_episode, make_env, score_episode


METRICS = ("tp", "fp", "fn", "precision", "recall", "f1", "return")
TYPES = ("duplicate_invoice", "payment_allocation", "fx_error", "period_error")


def read_json(path):
    return json.loads(path.read_text(encoding="utf8"))


def start(env, path, mode="closed"):
    return env.reset(71001, {"mode": mode, "episode_dir": str(path), "team_config": {}})


def step(env, name, **args):
    result = env.step({"name": name, "args": args})
    assert len(result) == 5
    obs, reward, done, truncated, info = result
    assert json.loads(json.dumps([obs, info], allow_nan=False)) == [obs, info]
    assert isinstance(reward, (float, int)) and not isinstance(reward, bool)
    assert isinstance(done, bool) and isinstance(truncated, bool)
    assert {"result", "error", "remaining_steps"} == set(obs)
    return result


@pytest.fixture
def clean(tmp_path):
    generate_episode(71419, {}, str(tmp_path))
    return tmp_path


def test_C01_C14_reset_pages_document_calculation_and_errors(clean):
    env = make_env()
    obs, info = start(env, clean)
    assert json.loads(json.dumps([obs, info], allow_nan=False)) == [obs, info]
    assert isinstance(obs["task"], str) and obs["task"] and obs["policy"]
    assert obs["remaining_steps"] == 100
    manifest = read_json(clean / "manifest.json")
    assert manifest == obs["manifest"] and obs["policy"] == read_json(clean / "policy.json")
    assert set(manifest) >= {"schema_version", "episode_id", "base_currency", "period_start",
                             "period_end", "timezone", "counts", "rounding"}
    with (clean / "ledger.csv").open(encoding="utf8", newline="") as f:
        expected_ids = {r["row_id"] for r in csv.DictReader(f)}
    assert manifest["counts"]["ledger_rows"] == len(expected_ids) >= 101
    cursor, seen = None, []
    while True:
        query = {"table": "ledger", "limit": 50, "cursor": cursor}
        page, reward, done, truncated, _ = step(env, "list_records", **query)
        assert (reward, done, truncated, page["error"]) == (0, False, False, None)
        assert 0 < len(page["result"]["records"]) <= 50
        again = step(env, "list_records", **query)[0]
        assert page["result"] == again["result"]
        assert page["remaining_steps"] == again["remaining_steps"] + 1
        seen.extend(r["row_id"] for r in page["result"]["records"])
        cursor = page["result"]["next_cursor"]
        if cursor is None:
            break
        assert isinstance(cursor, str)
    assert len(seen) == len(expected_ids) and set(seen) == expected_ids
    invoices = [json.loads(line) for line in (clean / "invoices.jsonl").read_text().splitlines()]
    assert manifest["counts"]["invoices"] == len(invoices)
    assert manifest["counts"]["payments"] == len((clean / "payments.jsonl").read_text().splitlines())
    with (clean / "fx.csv").open(encoding="utf8", newline="") as stream:
        assert manifest["counts"]["fx_rates"] == len(list(csv.DictReader(stream)))
    invoice = invoices[0]
    assert step(env, "get_record", table="invoices", id=invoice["invoice_id"])[0]["result"] == invoice
    assert step(env, "get_document", doc_id=invoice["doc_id"])[0]["result"] == invoice
    value = step(env, "calculator", expression="0.1 + 0.2")[0]
    assert isinstance(value["result"], str) and Decimal(value["result"]) == Decimal("0.3")
    remaining = value["remaining_steps"]
    for name, args in [("calculator", {"expression": "one plus two"}),
                       ("get_record", {"table": "ledger", "id": "absent"}),
                       ("get_document", {"doc_id": "absent"}), ("absent_action", {})]:
        error, reward, done, truncated, _ = step(env, name, **args)
        remaining -= 1
        assert error["result"] is None and error["remaining_steps"] == remaining
        assert error["error"]["code"] and error["error"]["message"]
        assert (reward, done, truncated) == (0, False, False)
    last = step(env, "get_document", doc_id=invoice["doc_id"])[0]
    assert last["remaining_steps"] == remaining - 1 and last["result"] == invoice


@pytest.mark.parametrize("submit_at_limit", [True, False], ids=["C15-C16", "C17"])
def test_step_limit_and_final_state(clean, submit_at_limit):
    env = make_env()
    obs, _ = start(env, clean, "train")
    answer = {"episode_id": obs["manifest"]["episode_id"], "findings": []}
    for j in range(99):
        page, reward, done, truncated, _ = step(env, "calculator", expression="1")
        assert (page["remaining_steps"], reward, done, truncated) == (99 - j, 0, False, False)
    if submit_at_limit:
        obs, reward, done, truncated, _ = step(env, "submit", findings=answer)
        assert obs["result"] == {"accepted": True}
        assert reward == pytest.approx(1 - 0.002 * 99)
    else:
        obs, reward, done, truncated, _ = step(env, "calculator", expression="1")
        assert reward == pytest.approx(0.8)
    assert (done, truncated) == (submit_at_limit, not submit_at_limit)
    frozen = step(env, "submit", findings=answer)
    assert frozen[0]["result"] is None and frozen[1:4] == (0, done, truncated)


def test_C18_timeout_observed_before_submit(tmp_path, monkeypatch):
    from proofledger import env as implementation

    env = make_env()
    generate_episode(18276, {"period_error": 1}, str(tmp_path))
    obs, _ = start(env, tmp_path, "train")
    issue, = read_json(tmp_path / "truth.json")["issues"]
    answer = {"episode_id": obs["manifest"]["episode_id"], "findings": [{
        "type": issue["type"], "affected_ids": issue["affected_ids"], "currency": issue["currency"],
        "claimed_amount_minor": issue["expected_amount_minor"], "evidence_ids": issue["evidence_sets"][0],
        "explanation": "open reference"}]}
    assert score_episode(str(tmp_path), answer, 0)["return"] == 1
    assert implementation.TIME_LIMIT_S == 60
    monkeypatch.setattr(implementation, "TIME_LIMIT_S", 0)
    result = step(env, "submit", findings=answer)
    assert result[1:4] == (-1, False, True) and result[0]["result"] is None


@pytest.mark.parametrize("mode", ["train", "closed"])
@pytest.mark.parametrize(("prior", "last", "inspections"), [
    ([], "submit", 0),
    (["calculator", "unknown_action"], "submit", 2),
    (["calculator"], "get_record", 2),
])
def test_C18_timeout_costs_only_inspection_calls(clean, monkeypatch, mode, prior, last, inspections):
    from proofledger import env as implementation

    env = make_env()
    obs, _ = start(env, clean, mode)
    empty = {"episode_id": obs["manifest"]["episode_id"], "findings": []}
    for name in prior:
        result = step(env, name, expression="1")
        assert result[1:4] == (0, False, False)
        assert (result[0]["error"] is None) == (name == "calculator")

    monkeypatch.setattr(implementation, "TIME_LIMIT_S", 0)
    args = {"findings": empty} if last == "submit" else {"table": "ledger", "id": "absent"}
    result, reward, terminated, truncated, info = step(env, last, **args)
    assert (terminated, truncated, result["result"], result["error"]["code"]) == (False, True, None, "truncated")
    assert info["step"] == len(prior) + 1 and result["remaining_steps"] == 100 - info["step"]
    score = score_episode(str(clean), empty, inspection_steps=inspections)["return"]
    assert score == pytest.approx(1 - 0.002 * inspections)
    assert reward == (score if mode == "train" else 0)
    assert step(env, "submit", findings=empty)[1:4] == (0, False, True)


@pytest.mark.parametrize("count", [0, 1], ids=["C20-clean", "C19-C20-dirty"])
def test_public_channel_and_modes(tmp_path, count):
    cfg = {"period_error": count}
    generate_episode(44012, cfg, str(tmp_path))
    truth = read_json(tmp_path / "truth.json")
    answer = {"episode_id": truth["episode_id"], "findings": []}
    private_markers = [truth["canary"], json.dumps(str(tmp_path))[1:-1], '"provenance"', '"seed"',
                       '"evidence_sets"', '"expected_amount_minor"',
                       *[i["issue_id"] for i in truth["issues"]]]

    def check_public(exchange):
        data = json.dumps(exchange, ensure_ascii=False)
        assert all(key not in data for key in private_markers)

    env = make_env()
    check_public(start(env, tmp_path))
    doc_ids = []
    for table in ("invoices", "payments", "ledger", "fx"):
        cursor = None
        while True:
            exchange = step(env, "list_records", table=table, cursor=cursor, limit=50)
            check_public(exchange)
            page = exchange[0]["result"]
            assert page and exchange[1:4] == (0, False, False)
            doc_ids.extend(r["doc_id"] for r in page["records"] if "doc_id" in r)
            cursor = page["next_cursor"]
            if cursor is None:
                break
    for offset in range(0, len(doc_ids), 90):
        start(env, tmp_path)
        for doc_id in doc_ids[offset:offset + 90]:
            exchange = step(env, "get_document", doc_id=doc_id)
            assert exchange[0]["result"]["doc_id"] == doc_id
            check_public(exchange)
    for mode in ("closed", "train"):
        start(env, tmp_path, mode)
        assert step(env, "calculator", expression="0")[1] == 0
        result = step(env, "submit", findings=answer)
        expected = score_episode(str(tmp_path), answer, 1)["return"]
        assert result[1:4] == (expected if mode == "train" else 0, True, False)
        assert result[0]["result"] == {"accepted": True}
        if mode == "closed":
            check_public(result)
            assert not ({"terminal", "report", *METRICS} & result[-1].keys())
    obs, _ = env.reset(44012, {"mode": "train", "team_config": cfg})
    assert obs["manifest"]["episode_id"] == truth["episode_id"]


@pytest.fixture
def scoring_case(tmp_path):
    """Judge-only labelled fixture. All target and evidence IDs come from generated open files."""
    generate_episode(23601, {}, str(tmp_path))
    with (tmp_path / "ledger.csv").open(encoding="utf8", newline="") as stream:
        rows = [row["row_id"] for row in csv.DictReader(stream)]
    docs = [json.loads(s)["doc_id"] for s in (tmp_path / "invoices.jsonl").read_text().splitlines()]
    eid = read_json(tmp_path / "manifest.json")["episode_id"]

    def issue(key="A", *, affected=None, evidence=None, tolerance=0):
        return {"issue_id": key, "type": "fx_error", "affected_ids": affected or rows[:1],
                "expected_amount_minor": 10000, "currency": "KZT",
                "evidence_sets": evidence or [docs[:2]], "tolerance_minor": tolerance}

    def finding(**updates):
        return {"type": "fx_error", "affected_ids": rows[:1], "claimed_amount_minor": 10000,
                "currency": "KZT", "evidence_ids": docs[:2], "explanation": "open test", **updates}

    def run(issues, findings, inspections=10):
        truth = {"episode_id": eid, "issues": issues}
        (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf8")
        policy_path = tmp_path / "policy.json"
        policy = read_json(policy_path)
        policy["amount_tolerance_minor"] = issues[0]["tolerance_minor"] if issues else 0
        policy_path.write_text(json.dumps(policy), encoding="utf8")
        answer = {"episode_id": eid, "findings": findings}
        score = score_episode(str(tmp_path), answer, inspections)
        assert set(score) == set(METRICS)
        if inspections < 100:  # R17 specifies an evaluator formula unit-test with 100 inspections.
            env = make_env()
            start(env, tmp_path, "train")
            for _ in range(inspections):
                assert step(env, "calculator", expression="0")[1] == 0
            obs, reward, done, truncated, _ = step(env, "submit", findings=answer)
            assert (done, truncated) == (True, False) and reward == score["return"]
            if score["tp"] is None:
                assert obs["result"] is None and obs["error"]["code"]
            else:
                assert obs["result"] == {"accepted": True} and obs["error"] is None
        return score

    return rows, docs, issue, finding, run


def require_score(score, expected):
    if expected is None:
        assert score == {key: -1 if key == "return" else None for key in METRICS}
        return
    tp, fp, fn, reward = expected
    for key, count in zip(METRICS[:3], (tp, fp, fn)):
        assert type(score[key]) is int and score[key] == count
    denominator = 2 * tp + fp + fn
    values = (tp / (tp + fp) if tp + fp else 0, tp / (tp + fn) if tp + fn else 0,
              2 * tp / denominator if denominator else 1, reward)
    for key, value in zip(METRICS[3:], values):
        assert score[key] == pytest.approx(value, abs=1e-9, rel=0)


WRONG = (0, 1, 1, -1)
RIGHT = (1, 0, 0, 0.98)


@pytest.mark.parametrize("n", range(1, 21), ids=lambda n: f"R{n:02}")
def test_reward_vectors(scoring_case, n):
    rows, docs, issue, f, run = scoring_case
    a, b = issue(), issue("B", affected=rows[1:2])
    cases = [
        ([a], [f()], 10, RIGHT),
        ([a], [], 10, (0, 0, 1, -1)),
        ([], [], 10, (0, 0, 0, .98)),
        ([], [f()], 10, (0, 1, 0, -1)),
        ([a], [f(), f()], 10, (1, 1, 0, 47 / 150)),
        ([a], [f(type="period_error")], 10, WRONG),
        ([a], [f(affected_ids=rows[1:2])], 10, WRONG),
        ([a], [f(claimed_amount_minor=10001)], 10, WRONG),
        ([a], [f(currency="USD")], 10, WRONG),
        ([a], [f(evidence_ids=docs[:1])], 10, WRONG),
        ([a], [f(evidence_ids=[docs[0], "absent"])], 10, WRONG),
        ([a], [f(affected_ids=[r], type=t) for r in rows[:5] for t in TYPES], 10, (1, 19, 0, -871 / 1050)),
        ([a, b], [f()], 10, (1, 0, 1, 47 / 150)),
        ([a, b], [f(), f(affected_ids=rows[2:3])], 10, (1, 1, 1, -.02)),
        ([a], [f()], 0, (1, 0, 0, 1)),
        ([a], [f()], 50, (1, 0, 0, .9)),
        ([a], [f()], 100, (1, 0, 0, .8)),
        ([a], [f()] * 31, 10, None),
        ([a], [f(evidence_ids=docs[:11])], 10, None),
        ([a], [f(explanation="x" * 1001)], 10, None),
    ]
    truth, findings, steps, expected = cases[n - 1]
    require_score(run(truth, findings, steps), expected)


@pytest.mark.parametrize("n", range(1, 13), ids=lambda n: f"M{n:02}")
def test_matcher_examples(scoring_case, n):
    rows, docs, issue, finding, run = scoring_case
    a = issue(affected=rows[:2], tolerance=int(n == 8))
    def f(**updates):
        return finding(**{"affected_ids": rows[:2], **updates})

    if n in (10, 11):
        # F0 fits both, F1 fits A only (M10) or both (M11).
        a["evidence_sets"] = [[docs[0]], [docs[1]]]
        b_sets = [docs[:1]] if n == 10 else a["evidence_sets"]
        b = issue("B", affected=rows[:2], evidence=b_sets)
        findings = [f(evidence_ids=docs[:1]), f(evidence_ids=docs[1:2])]
        for submitted in (findings, list(reversed(findings))):
            require_score(run([b, a], submitted), (2, 0, 0, .98))
        return

    examples = {
        1: [([f(affected_ids=[rows[1], rows[0], rows[0]])], RIGHT)],
        2: [([f(affected_ids=rows[:3])], WRONG)],
        3: [([f(evidence_ids=[docs[1], docs[0], docs[2]])], RIGHT)],
        4: [([f(evidence_ids=docs[:2] + ["absent"])], WRONG)],
        5: [([f(evidence_ids=[docs[0]] * 10 + docs[1:2])], None)],
        6: [([f(currency=c)], WRONG) for c in ("kzt", "KZT ")],
        7: [([f(claimed_amount_minor=10001)], WRONG)],
        8: [([f(claimed_amount_minor=10001)], RIGHT), ([f(claimed_amount_minor=10002)], WRONG)],
        9: [([f(claimed_amount_minor=v)], None) for v in ("10000", True)],
        12: [([f(), f()], (1, 1, 0, 47 / 150))],
    }
    for submitted, expected in examples[n]:
        require_score(run([a], submitted), expected)
