"""Tests for the v0.2 families: allocation, period cutoff, mixed scenarios, distractors, relabel."""
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from proofledger import generate_episode
from proofledger.generator import _self_check
from proofledger.policies import run_policy

MAX12 = {"duplicate_invoice": 2, "payment_allocation": 2, "fx_error": 2, "period_error": 2,
         "mix_fx_allocation": 1, "mix_fx_period": 1}
ALL = {"duplicate_invoice": 1, "payment_allocation": 1, "fx_error": 1, "period_error": 1,
       "mix_fx_allocation": 1, "mix_fx_period": 1}


def company_date(ts, offset):
    # independent of proofledger.analysis on purpose
    sign = -1 if offset[0] == "-" else 1
    h, m = map(int, offset[1:].split(":"))
    return datetime.fromisoformat(ts).astimezone(timezone(sign * timedelta(hours=h, minutes=m))).date()


def by_type(ep, t):
    return [i for i in ep["truth"]["issues"] if i["type"] == t]


def test_payment_allocation_amounts_from_ledger():
    for s in range(15):
        ep = generate_episode(s, {"payment_allocation": 3})
        pays = {p["payment_id"]: p for p in ep["payments"]}
        for i in by_type(ep, "payment_allocation"):
            pid = i["affected_ids"][0]
            rows = [r for r in ep["ledger"] if r["payment_id"] == pid]
            assert sum(r["credit_minor"] for r in rows if r["account"] == "2900") == i["expected_amount_minor"]
            cash = sum(r["debit_minor"] for r in rows if r["account"] == "1010")
            assert 0 < i["expected_amount_minor"] <= cash
            assert pays[pid]["doc_id"] in i["evidence_sets"][0]


def test_missing_allocation_has_explicit_null_link():
    kinds = Counter()
    for s in range(20):
        ep = generate_episode(s, {"payment_allocation": 2})
        for p in ep["provenance"]["issues"]:
            if p["type"] == "payment_allocation":
                kinds[p["cause"]] += 1
                pid = next(i for i in ep["truth"]["issues"] if i["issue_id"] == p["issue_id"])["affected_ids"][0]
                doc = next(x for x in ep["payments"] if x["payment_id"] == pid)
                assert (doc["invoice_refs"] is None) == (p["cause"] == "missing_allocation")
    assert kinds["missing_allocation"] and kinds["partial_allocation"]


def test_period_error_issued_after_period_booked_inside():
    for s in range(15):
        ep = generate_episode(s, {"period_error": 3})
        m = ep["manifest"]
        pe = date.fromisoformat(m["period_end"])
        inv = {i["invoice_id"]: i for i in ep["invoices"]}
        for i in by_type(ep, "period_error"):
            iid = i["affected_ids"][0]
            assert company_date(inv[iid]["issued_at"], m["timezone"]) > pe
            rev = [r for r in ep["ledger"] if r["invoice_id"] == iid and r["account"] == "6010"]
            assert len(rev) == 1 and rev[0]["credit_minor"] == i["expected_amount_minor"]
            assert company_date(rev[0]["timestamp"], m["timezone"]) <= pe


def test_mixed_scenarios_hit_the_same_record():
    for s in range(10):
        ep = generate_episode(s, {"mix_fx_period": 1})
        fx, per = by_type(ep, "fx_error"), by_type(ep, "period_error")
        assert len(fx) == len(per) == 1 and fx[0]["affected_ids"] == per[0]["affected_ids"]
        ep = generate_episode(s, {"mix_fx_allocation": 1})
        fx, al = by_type(ep, "fx_error"), by_type(ep, "payment_allocation")
        pay = next(p for p in ep["payments"] if p["payment_id"] == al[0]["affected_ids"][0])
        assert pay["invoice_refs"] == fx[0]["affected_ids"]
        assert next(p for p in ep["provenance"]["issues"] if p["type"] == "payment_allocation")["mixed"]


def test_boundary_distractors_fool_naive_dates_but_are_clean():
    hits = 0
    for s in range(15):
        ep = generate_episode(s, {"boundary_distractors": 4})
        m = ep["manifest"]
        ps, pe = date.fromisoformat(m["period_start"]), date.fromisoformat(m["period_end"])
        assert ep["truth"]["issues"] == []
        for i in ep["invoices"]:
            naive = date.fromisoformat(i["issued_at"][:10])
            real = company_date(i["issued_at"], m["timezone"])
            if not ps <= naive <= pe and ps <= real <= pe:
                hits += 1
    assert hits >= 10


def test_refunds_are_legitimate():
    seen = 0
    for s in range(10):
        ep = generate_episode(s, {"refund_rate": 0.3})
        refunds = [p for p in ep["payments"] if p["amount_minor"] < 0]
        seen += len(refunds)
        assert ep["truth"]["issues"] == []
        assert run_policy("reconciler", s, {"refund_rate": 0.3})["report"]["fp"] == 0
    assert seen > 0


@pytest.mark.parametrize("seed", range(15))
def test_twelve_errors_solvable(seed):
    ep = generate_episode(seed, MAX12)
    assert len(ep["truth"]["issues"]) == 12
    assert run_policy("reconciler", seed, MAX12)["report"]["f1"] == 1.0


def test_relabel_changes_ids_and_order_but_not_content():
    for s in range(8):
        a, b = generate_episode(s, ALL), generate_episode(s, {**ALL, "relabel": 3})
        assert a["manifest"]["episode_id"] != b["manifest"]["episode_id"]
        assert {i["invoice_id"] for i in a["invoices"]} != {i["invoice_id"] for i in b["invoices"]}
        assert [i["total_minor"] for i in a["invoices"]] != [i["total_minor"] for i in b["invoices"]] or s > 0
        assert sorted(i["total_minor"] for i in a["invoices"]) == sorted(i["total_minor"] for i in b["invoices"])
        key = lambda e: sorted((i["type"], i["expected_amount_minor"], i["currency"]) for i in e["truth"]["issues"])
        assert key(a) == key(b)
        for p in ("oracle", "reconciler", "flag_all", "silence"):
            x, y = run_policy(p, s, ALL)["report"], run_policy(p, s, {**ALL, "relabel": 3})["report"]
            assert (x["reward"], x["tp"], x["fp"], x["fn"]) == (y["reward"], y["tp"], y["fp"], y["fn"]), p


def test_canary_never_reaches_agent():
    for s in range(5):
        res = run_policy("reconciler", s, ALL)
        canary = generate_episode(s, ALL)["truth"]["canary"]
        assert canary.startswith("PLX-CANARY-")
        assert "PLX-CANARY" not in json.dumps(res["trajectory"])


def test_self_check_rejects_tampered_truth():
    ep = generate_episode(0, ALL)
    ep["truth"]["issues"][0]["expected_amount_minor"] += 10 ** 6
    with pytest.raises(RuntimeError):
        _self_check(ep)


def test_export(tmp_path):
    from proofledger import export
    assert export.main([str(tmp_path)]) == 0
    idx = json.loads((tmp_path / "configs.json").read_text())
    assert len(idx["train"]) == len(idx["dev"]) == 10
    assert len({e["episode_id"] for e in idx["train"] + idx["dev"]}) == 20
    blob = (tmp_path / "trajectories.jsonl").read_text()
    rows = [json.loads(x) for x in blob.splitlines()]
    assert {"episode_id", "step", "action", "observation", "reward", "terminated", "truncated"} <= set(rows[0])
    assert "ISS-" not in blob and "PLX-CANARY" not in blob and "evidence_sets" not in blob
