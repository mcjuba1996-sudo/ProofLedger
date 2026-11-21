import json
from datetime import datetime

import pytest

from proofledger import analysis, generate_episode

CFGS = [{}, {"duplicate_invoice": 3}, {"fx_error": 3}, {"duplicate_invoice": 2, "fx_error": 2},
        {"duplicate_invoice": 6, "fx_error": 6, "n_invoices": 120}]


def dump(ep):
    return json.dumps(ep, sort_keys=True)


def test_determinism():
    assert dump(generate_episode(5, CFGS[3])) == dump(generate_episode(5, CFGS[3]))


def test_seeds_differ():
    a, b = generate_episode(1), generate_episode(2)
    assert a["manifest"]["episode_id"] != b["manifest"]["episode_id"]
    assert a["invoices"] != b["invoices"]


@pytest.mark.parametrize("cfg", CFGS)
def test_discrepancies_equal_truth(cfg):
    for seed in range(12):
        ep = generate_episode(seed, cfg)
        tz = ep["manifest"]["timezone"]
        issues = ep["truth"]["issues"]
        assert len(issues) == cfg.get("duplicate_invoice", 0) + cfg.get("fx_error", 0)
        dups = {tuple(x["invoice_id"] for x in g) for g in analysis.duplicate_groups(ep["invoices"], tz)}
        assert dups == {tuple(i["affected_ids"]) for i in issues if i["type"] == "duplicate_invoice"}
        fxm = {m["invoice_id"]: m for m in analysis.fx_mismatches(ep["invoices"], ep["ledger"], ep["fx"], tz)}
        for i in issues:
            if i["type"] == "fx_error":
                assert fxm[i["affected_ids"][0]]["diff"] == i["expected_amount_minor"]
        assert len(fxm) == cfg.get("fx_error", 0)


def test_ledger_balanced_and_integer_money():
    for seed in range(10):
        ep = generate_episode(seed, CFGS[3])
        assert sum(r["debit_minor"] for r in ep["ledger"]) == sum(r["credit_minor"] for r in ep["ledger"])
        for r in ep["ledger"]:
            assert isinstance(r["debit_minor"], int) and isinstance(r["credit_minor"], int)
        for r in ep["invoices"] + ep["payments"]:
            assert isinstance(r.get("total_minor", r.get("amount_minor")), int)


def test_clean_episode_has_distractors_but_no_issues():
    for seed in range(10):
        ep = generate_episode(seed, {"recurring_entities": 2})
        assert ep["truth"]["issues"] == []
        seen = {}
        for i in ep["invoices"]:
            seen.setdefault((i["entity_id"], i["total_minor"]), []).append(i)
        assert any(len(v) >= 3 for v in seen.values())  # legit recurring identical amounts
        assert any(len(p["invoice_refs"]) == 1 for p in ep["payments"])
        paid = {}
        for p in ep["payments"]:
            paid.setdefault(p["invoice_refs"][0], []).append(p)
        assert any(len(v) == 2 for v in paid.values()) or seed > 0  # split payments occur


def test_evidence_ids_resolve():
    ep = generate_episode(3, CFGS[3])
    known = ({i["doc_id"] for i in ep["invoices"]} | {p["doc_id"] for p in ep["payments"]}
             | {r["row_id"] for r in ep["ledger"]}
             | {f"{r['date']}|{r['source_currency']}|{r['target_currency']}" for r in ep["fx"]})
    for i in ep["truth"]["issues"]:
        for es in i["evidence_sets"]:
            assert set(es) <= known


def test_null_links_and_timestamps():
    ep = generate_episode(0, CFGS[3])
    assert any(r["payment_id"] is None for r in ep["ledger"])
    assert all(r["payment_id"] != "" for r in ep["ledger"])
    stamps = [r["timestamp"] for r in ep["ledger"]] + [i["issued_at"] for i in ep["invoices"]]
    assert all(datetime.fromisoformat(s).tzinfo is not None for s in stamps)
    assert len({s[-6:] for s in stamps}) > 1  # several timezone offsets


def test_default_size_in_range():
    for seed in range(10):
        ep = generate_episode(seed)
        assert 200 <= len(ep["ledger"]) <= 2000 and 20 <= len(ep["invoices"]) + len(ep["payments"]) <= 200 + 100


@pytest.mark.parametrize("bad", [{"nope": 1}, {"duplicate_invoice": 7, "fx_error": 6},
                                 {"duplicate_invoice": -1}, {"n_entities": 1}, {"unpaid_rate": 2}])
def test_bad_config(bad):
    with pytest.raises(ValueError):
        generate_episode(0, bad)
