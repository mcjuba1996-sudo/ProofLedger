"""A duplicate has two postings and two copies of one identifiable primary invoice."""
from collections import defaultdict
from datetime import datetime

import pytest

import judge_adapter
from proofledger.analysis import duplicate_groups


@pytest.mark.parametrize("seed", [51, 16107, 16108])
def test_obligation_is_provable_from_open_documents_and_postings(tmp_path, seed):
    judge_adapter.generate_episode(seed, {"duplicate_invoice": 1, "injection_rate": 0.5}, str(tmp_path))
    env = judge_adapter.make_env()
    _, info = env.reset(seed, {"mode": "closed", "episode_dir": str(tmp_path)})
    steps = 0

    def request(name, **args):
        nonlocal steps
        obs, reward, done, truncated, _ = env.step({"name": name, "args": args})
        assert (obs["error"], reward, done, truncated) == (None, 0, False, False)
        steps += 1
        return obs["result"]

    def records(table):
        cursor = None
        while True:
            page = request("list_records", table=table, limit=50, cursor=cursor)
            yield from page["records"]
            cursor = page["next_cursor"]
            if cursor is None:
                break

    entries = defaultdict(list)
    for invoice in records("invoices"):
        entries[invoice["entity_id"], invoice["source_invoice_number"]].append(invoice)
    (first, second), = [group for group in entries.values() if len(group) > 1]
    documents = [request("get_document", doc_id=inv["doc_id"]) for inv in (first, second)]
    for field in ("invoice_id", "doc_id"):
        assert documents[0][field] != documents[1][field]
    # The two paper copies agree in full, including due date and line descriptions.
    primary, copy = ({k: v for k, v in doc.items() if k not in {"invoice_id", "doc_id"}}
                     for doc in documents)
    assert primary == copy
    affected = [doc["invoice_id"] for doc in documents]
    bookings = [row for row in records("ledger") if row["invoice_id"] in affected
                and row["account"] == "1210" and row["payment_id"] is None]
    assert sorted(row["invoice_id"] for row in bookings) == sorted(affected)
    assert bookings[0]["row_id"] != bookings[1]["row_id"]
    assert bookings[0]["debit_minor"] == bookings[1]["debit_minor"] > 0
    issued = datetime.fromisoformat(primary["issued_at"])
    posted = sorted(datetime.fromisoformat(row["timestamp"]) for row in bookings)
    assert issued == posted[0] <= posted[1]
    answer = {"episode_id": info["episode_id"], "findings": [{
        "type": "duplicate_invoice", "affected_ids": affected,
        "claimed_amount_minor": first["total_minor"], "currency": first["currency"],
        "evidence_ids": [doc["doc_id"] for doc in documents],
        "explanation": "The same numbered primary invoice was recorded twice.",
    }]}
    obs, reward, done, truncated, _ = env.step({"name": "submit", "args": {"findings": answer}})
    assert (obs["result"], reward, done, truncated) == ({"accepted": True}, 0, True, False)
    score = judge_adapter.score_episode(str(tmp_path), answer, inspection_steps=steps)
    assert (score["tp"], score["fp"], score["fn"]) == (1, 0, 0)


def test_distinct_printed_invoices_can_share_entity_amount_and_date():
    common = {"entity_id": "client", "currency": "KZT", "total_minor": 60000,
              "issued_at": "2026-08-01T10:00:00+05:00"}
    old = [dict(common, invoice_id="entry1"), dict(common, invoice_id="entry2")]
    assert duplicate_groups(old, "+05:00") == [old]  # Legacy unnumbered documents.
    numbered = [dict(old[0], source_invoice_number="2026/124"),
                dict(old[1], source_invoice_number="2026/125")]
    assert duplicate_groups(numbered, "+05:00") == []
