"""Data integrity: accounting equalities before error injection, and the declared difference after.

restore_clean(ep) undoes every change recorded in provenance.json and returns the books as they
were before injection. check_books(ep) lists every violated accounting rule. For a valid episode:
  * check_books(restore_clean(ep)) == []                 (clean books are consistent)
  * every issue in truth.json has at least one recorded change, and nothing else was changed.
python -m proofledger.integrity [--seeds N]
"""
import argparse
import copy
import sys
from datetime import date

from . import analysis
from .analysis import AR, CASH, REVENUE, SUSPENSE
from .money import BASE_CURRENCY, convert_minor


def restore_clean(ep):
    ep = copy.deepcopy(ep)
    rows = {r["row_id"]: r for r in ep["ledger"]}
    drop_rows, drop_inv = set(), set()
    pays = {p["payment_id"]: p for p in ep["payments"]}
    for p in ep["provenance"]["issues"]:
        for c in p.get("changes", []):
            op = c["op"]
            if op == "insert_invoice":
                drop_inv.add(c["invoice_id"])
                drop_rows.update(c["ledger_rows"])
            elif op == "insert_early":
                drop_rows.update(c["ledger_rows"])
            elif op == "update_amount":
                ar, rev = (rows.get(x) for x in c["ledger_rows"])
                if ar:
                    ar["debit_minor"] = c["before"]
                if rev:
                    rev["credit_minor"] = c["before"]
            elif op == "misallocate":
                drop_rows.add(c["suspense_row"])
                cash = rows[c["cash_row"]]
                cash["invoice_id"] = c["invoice_id"]
                pays[c["payment_id"]]["invoice_refs"] = [c["invoice_id"]]
                if c["ar_row"]:
                    rows[c["ar_row"]]["credit_minor"] = c["ar_credit_before"]
                else:
                    ep["ledger"].append(dict(cash, row_id=c["suspense_row"] + "-restored", account=AR, debit_minor=0,
                                             credit_minor=c["ar_credit_before"]))
            else:
                raise ValueError(f"unknown change op {op}")
    ep["ledger"] = [r for r in ep["ledger"] if r["row_id"] not in drop_rows]
    ep["invoices"] = [i for i in ep["invoices"] if i["invoice_id"] not in drop_inv]
    return ep


def check_books(ep):
    """Return a list of violated accounting rules (empty list = consistent books)."""
    m, bad = ep["manifest"], []
    tz = m["timezone"]
    ps, pe = date.fromisoformat(m["period_start"]), date.fromisoformat(m["period_end"])
    rates = analysis.rate_map(ep["fx"])
    inv = {i["invoice_id"]: i for i in ep["invoices"]}
    led = ep["ledger"]
    if sum(r["debit_minor"] for r in led) != sum(r["credit_minor"] for r in led):
        bad.append("ledger debits != credits")
    if any(r["currency"] != BASE_CURRENCY for r in led):
        bad.append("ledger row not in base currency")
    ar, rev = analysis.invoice_rows(led)
    for iid, r in ar.items():
        i = inv.get(iid)
        if i is None:
            bad.append(f"{iid}: posted but no invoice document")
            continue
        d = analysis.local_date(i["issued_at"], tz)
        if not ps <= d <= pe:
            bad.append(f"{iid}: issued {d} outside the period but posted")
        rate = rates.get((d.isoformat(), i["currency"]))
        want = convert_minor(i["total_minor"], i["currency"], BASE_CURRENCY, rate)
        if r["debit_minor"] != want or rev.get(iid, {}).get("credit_minor") != want:
            bad.append(f"{iid}: AR/revenue {r['debit_minor']} != policy amount {want}")
    if set(rev) != set(ar):
        bad.append("AR and revenue postings do not pair up")
    if any(r["account"] == SUSPENSE for r in led):
        bad.append("unallocated receipts present")
    by_pay = {}
    for r in led:
        if r["payment_id"]:
            by_pay.setdefault(r["payment_id"], []).append(r)
    for p in ep["payments"]:
        rs = by_pay.get(p["payment_id"], [])
        refs = p["invoice_refs"]
        if not refs:
            bad.append(f"{p['payment_id']}: payment without invoice reference")
            continue
        cash = sum(r["debit_minor"] - r["credit_minor"] for r in rs if r["account"] == CASH)
        arc = sum(r["credit_minor"] - r["debit_minor"] for r in rs if r["account"] == AR)
        if cash != arc:
            bad.append(f"{p['payment_id']}: cash {cash} != AR settlement {arc}")
        if any(r["invoice_id"] not in refs for r in rs):
            bad.append(f"{p['payment_id']}: posted to an invoice it does not reference")
        if any(inv.get(x) and inv[x]["currency"] != p["currency"] for x in refs):
            bad.append(f"{p['payment_id']}: currency differs from invoice")
    for g in analysis.duplicate_groups(ep["invoices"], tz):
        bad.append("duplicate invoices " + ",".join(x["invoice_id"] for x in g))
    return bad


def check_episode(ep):
    """Integrity of one generated episode; returns a list of problems (empty = valid)."""
    problems = [f"clean state: {x}" for x in check_books(restore_clean(ep))]
    issues = {i["issue_id"] for i in ep["truth"]["issues"]}
    changed = {p["issue_id"] for p in ep["provenance"]["issues"] if p.get("changes")}
    if issues != changed:
        problems.append("issues without recorded changes or changes without issues")
    if ep["truth"]["issues"] and not check_books(ep):
        problems.append("errors were injected but the books show no violation")
    return problems


def main(argv=None):
    from .generator import generate_episode
    from .levels import LEVEL_IDS, level_config
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    n = ap.parse_args(argv).seeds
    total = bad = 0
    for lv in LEVEL_IDS:
        for s in range(n):
            ep = generate_episode(s, level_config(lv, s))
            pr = check_episode(ep)
            total += 1
            if pr:
                bad += 1
                print(lv, s, pr[:3])
    print(f"{total - bad}/{total} episodes: clean books consistent, every issue traced to a recorded change")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
