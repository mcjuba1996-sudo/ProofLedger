"""Twenty local D/P fixtures following the public rubric. Seeds here are public."""
import itertools
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import pytest

from judge_adapter import generate_episode, make_env, score_episode
from proofledger.generator import read_episode
from proofledger.integrity import check_books, restore_clean
from proofledger.judge_benchmark import EXPLORER_SEED, SCENARIOS, SEED_BASE, run_public_baseline
from proofledger.policies import FlagAll, RandomExplorer, Reconciler


TABLE_KEYS = {"invoices": "invoice_id", "payments": "payment_id", "ledger": "row_id", "fx": "key"}


def load(env, folder, seed):
    return env.reset(seed, {"mode": "closed", "episode_dir": str(folder), "team_config": {}})


def findings_from_truth(truth):
    return {"episode_id": truth["episode_id"], "findings": [
        {"type": i["type"], "affected_ids": i["affected_ids"], "currency": i["currency"],
         "claimed_amount_minor": i["expected_amount_minor"], "evidence_ids": i["evidence_sets"][0],
         "explanation": "reference"} for i in truth["issues"]]}


def reference_run(env, folder, seed):
    obs, info = load(env, folder, seed)
    trace, tables, evidence = [(obs, info)], {}, set()
    for name, key in TABLE_KEYS.items():
        table = {}
        cursor = None
        while True:
            action = {"name": "list_records", "args": {"table": name, "cursor": cursor, "limit": 50}}
            exchange = env.step(action)
            trace.append((action, exchange))
            assert exchange[1:4] == (0, False, False) and exchange[0]["error"] is None
            page = exchange[0]["result"]
            for record in page["records"]:
                identifier = record[key]
                assert identifier not in table
                table[identifier] = record
                evidence.add(record.get("doc_id", identifier))
            cursor = page["next_cursor"]
            if cursor is None:
                break
        tables[name] = table
    truth = json.loads((folder / "truth.json").read_text(encoding="utf8"))
    assert all(set(group) <= evidence for issue in truth["issues"] for group in issue["evidence_sets"])
    answer = findings_from_truth(truth)
    steps = len(trace) - 1
    action = {"name": "submit", "args": {"findings": answer}}
    exchange = env.step(action)
    trace.append((action, exchange))
    assert exchange[0]["result"] == {"accepted": True} and exchange[1:4] == (0, True, False)
    score = score_episode(str(folder), answer, steps)
    assert (score["tp"], score["fp"], score["fn"]) == (len(truth["issues"]), 0, 0)
    assert score["f1"] == 1 and score["return"] == pytest.approx(1 - .002 * steps)
    return trace, tables, answer, score


def countercheck(tables, manifest):
    """Independently derive all four classes, amounts and currencies from public tables."""
    tz = datetime.fromisoformat("2000-01-01T00:00:00" + manifest["timezone"]).tzinfo
    invoices, ledger = tables["invoices"], tables["ledger"].values()
    assert sum(row["debit_minor"] - row["credit_minor"] for row in ledger) == 0
    base, ending = manifest["base_currency"], manifest["period_end"]
    exponents = manifest["rounding"]["minor_unit_exponent"]
    amounts_by_id = defaultdict(list)
    for r in ledger:
        if r["account"] == "1210" and r["payment_id"] is None:
            amounts_by_id[r["invoice_id"]].append(r)
    by_key = defaultdict(list)
    findings = []
    for iid, inv in invoices.items():
        day = datetime.fromisoformat(inv["issued_at"]).astimezone(tz).date().isoformat()
        amount, currency = inv["total_minor"], inv["currency"]
        assert amount == sum(x["unit_price_minor"] * x["quantity"] for x in inv["line_items"])
        by_key[(inv["entity_id"], inv["source_invoice_number"])].append(iid)
        posting, = amounts_by_id[iid]
        booked = posting["debit_minor"]
        if currency != base:
            fx = tables["fx"][f"{day}|{currency}|{base}"]
            numeric = Decimal(amount) * Decimal(fx["rate"]) * Decimal(10) ** (exponents[base] - exponents[currency])
            correct = int(numeric.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if booked != correct:
                findings.append(("fx_error", (iid,), abs(booked - correct), base))
        if day > ending:
            assert datetime.fromisoformat(posting["timestamp"]).astimezone(tz).date().isoformat() <= ending
            findings.append(("period_error", (iid,), booked, base))
    for ids in by_key.values():
        if len(ids) > 1:
            assert len(ids) == 2
            original, copied = (invoices[i] for i in ids)
            assert original["doc_id"] != copied["doc_id"]
            for field in ("issued_at", "due_at", "line_items", "total_minor", "currency"):
                assert original[field] == copied[field]
            findings.append(("duplicate_invoice", tuple(sorted(ids)), original["total_minor"], original["currency"]))
    suspense = Counter()
    for row in ledger:
        if row["account"] == "2900":
            suspense[row["payment_id"]] += row["credit_minor"] - row["debit_minor"]
    for pid, unallocated in suspense.items():
        assert pid in tables["payments"] and unallocated > 0
        findings.append(("payment_allocation", (pid,), unallocated, base))
    return sorted(findings)


def external_variant(folder, target, n, tables):
    """Test harness controls this file transform independently of the implementation."""
    rename, shuffle = n > 5, n <= 5 or n > 10
    rng = random.Random(313000 + n)
    target.mkdir()
    # In this schema the surrogate ID namespaces have disjoint strings. FX composite
    # keys retain the date and currency pair, and meaningful line-item order is kept.
    ids = set()
    for record in itertools.chain.from_iterable(t.values() for t in tables.values()):
        ids.update(record[k] for k in ("doc_id", "invoice_id", "payment_id", "entity_id", "row_id")
                   if record.get(k) is not None)
    ids.update(i["issue_id"] for i in json.loads((folder / "truth.json").read_text())["issues"])
    replacements = list(range(len(ids)))
    rng.shuffle(replacements)
    mapping = {key: f"publicID_{value:04d}" for key, value in zip(sorted(ids), replacements)} if rename else {}
    tokens = re.compile(r"\b(?:INV-\d+|PAY-\d+|A-\d+|P-\d+|E-\d+|ISS-\d+|L\d+)\b")
    for original in folder.iterdir():
        content = original.read_text(encoding="utf8")
        if rename:
            content = tokens.sub(lambda m: mapping[m[0]], content)
        if shuffle and original.suffix in {".csv", ".jsonl"}:
            lines = content.splitlines()
            header_size = int(original.suffix == ".csv")
            records = lines[header_size:]
            rng.shuffle(records)
            content = "\n".join(lines[:header_size] + records) + "\n"
        (target / original.name).write_text(content, encoding="utf8")
    return {value: key for key, value in mapping.items()}


def undo_ids(value, inverse):
    if not inverse:
        return value
    # Synthetic replacement tokens are globally distinct; JSON escaping does not change them.
    restored = re.sub(r"\bpublicID_\d+\b", lambda m: inverse[m[0]], json.dumps(value))
    return json.loads(restored)


def check_clean_scenario(n, tables, manifest):
    """Confirm the intended legitimate distractor is actually present."""
    invoices, pays = tables["invoices"], tables["payments"].values()
    if n == 2:
        parts = defaultdict(list)
        for p in pays:
            parts[p["invoice_refs"][0]].append(p["amount_minor"])
        assert any(len(v) > 1 and sum(v) == invoices[i]["total_minor"] for i, v in parts.items())
    if n == 3:
        amounts = Counter((p["invoice_refs"][0], p["amount_minor"]) for p in pays)
        assert any(a < 0 and amounts[i, -a] >= 2 for i, a in amounts)
    if n == 4:
        dates = defaultdict(set)
        for inv in invoices.values():
            key = (inv["entity_id"], inv["currency"], inv["total_minor"])
            dates[key].add(inv["issued_at"][:10])
        assert any(len(days) >= 4 for days in dates.values())
    if n == 5:
        assert any(inv["currency"] != manifest["base_currency"]
                   and not manifest["period_start"] <= inv["issued_at"][:10] <= manifest["period_end"]
                   for inv in invoices.values())


@pytest.mark.parametrize("n", range(1, 21), ids=lambda n: f"D{n:02}-P{n:02}")
def test_data_and_pairs(n, tmp_path):
    seed = SEED_BASE + n
    source, changed = tmp_path / "original", tmp_path / "transformed"
    generate_episode(seed, SCENARIOS[n - 1], str(source))
    ep = read_episode(source)
    assert not check_books(restore_clean(ep))
    issues = ep["truth"]["issues"]
    assert len(issues) == (0 if n <= 5 else 1 if n <= 15 else 2)
    assert {i["issue_id"] for i in issues} == {
        p["issue_id"] for p in ep["provenance"]["issues"] if p["changes"]}
    env = make_env()
    trace, tables, answer, score = reference_run(env, source, seed)
    assert trace == reference_run(env, source, seed)[0]
    expected = sorted((i["type"], tuple(sorted(i["affected_ids"])), i["expected_amount_minor"], i["currency"])
                      for i in issues)
    assert countercheck(tables, ep["manifest"]) == expected
    if n <= 5:
        check_clean_scenario(n, tables, ep["manifest"])
    if 6 <= n <= 15:
        assert {i["type"] for i in issues} == set(SCENARIOS[n - 1])
    if n in (9, 11):
        assert ep["provenance"]["issues"][0]["cause"] == ("partial_allocation" if n == 9 else "missing_allocation")
    if n >= 16:
        by_type = {i["type"]: i["affected_ids"] for i in issues}
        if n < 19:
            payment, = by_type["payment_allocation"]
            assert tables["payments"][payment]["invoice_refs"] == by_type["fx_error"]
        else:
            assert by_type["period_error"] == by_type["fx_error"]
    for agent in (Reconciler(), RandomExplorer(EXPLORER_SEED), FlagAll()):
        scored = run_public_baseline(agent, source, seed)
        assert scored["f1"] == (1 if agent.name == "reconciler" or (agent.name == "random_explorer" and not issues) else 0)
    inverse = external_variant(source, changed, n, tables)
    other_trace, other_tables, other_answer, other_score = reference_run(env, changed, seed)
    assert len(trace) == len(other_trace)  # equal inspection costs
    assert tables == undo_ids(other_tables, inverse)
    assert answer == undo_ids(other_answer, inverse)
    assert score == other_score
