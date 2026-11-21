"""Record analysis helpers: used by the reconciler policy and by the generator's solvability check.

Everything here reads only the open records (manifest, invoices, payments, ledger, fx).
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .money import BASE_CURRENCY, convert_minor

AR, REVENUE, CASH, SUSPENSE = "1210", "6010", "1010", "2900"


def tz_from_offset(s: str):
    sign = -1 if s[0] == "-" else 1
    h, m = s.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def local_date(ts: str, tz_offset):
    """Calendar date of an ISO-8601 timestamp in the company timezone.

    tz_offset=None is the deliberately naive reading (date printed in the string), used only by
    the diagnostic naive_reconciler to measure what the timezone traps catch.
    """
    if tz_offset is None:
        return date.fromisoformat(ts[:10])
    return datetime.fromisoformat(ts).astimezone(tz_from_offset(tz_offset)).date()


def rate_map(fx):
    return {(r["date"], r["source_currency"]): Decimal(r["rate"]) for r in fx}


def invoice_rows(ledger):
    """AR debit and revenue credit rows of each invoice posting (payment_id is null)."""
    ar, rev = {}, {}
    for r in ledger:
        if r["invoice_id"] is None or r["payment_id"] is not None:
            continue
        if r["account"] == AR:
            ar[r["invoice_id"]] = r
        elif r["account"] == REVENUE:
            rev[r["invoice_id"]] = r
    return ar, rev


def duplicate_groups(invoices, tz_offset):
    """Group multiple entries for one primary document, including legacy unnumbered invoices."""
    groups = {}
    for inv in invoices:
        if "source_invoice_number" in inv:
            k = (inv["entity_id"], inv["source_invoice_number"])
        else:
            k = (inv["entity_id"], inv["currency"], inv["total_minor"],
                 local_date(inv["issued_at"], tz_offset).isoformat())
        groups.setdefault(k, []).append(inv)
    return [sorted(g, key=lambda i: i["invoice_id"]) for k, g in sorted(groups.items()) if len(g) > 1]


def fx_mismatches(invoices, ledger, fx, tz_offset, tolerance=1):
    """Foreign-currency invoices whose AR booking differs from the policy rate by > tolerance."""
    rates = rate_map(fx)
    ar, rev = invoice_rows(ledger)
    out = []
    for inv in sorted(invoices, key=lambda i: i["invoice_id"]):
        cur = inv["currency"]
        if cur == BASE_CURRENCY or inv["invoice_id"] not in ar:
            continue
        d = local_date(inv["issued_at"], tz_offset).isoformat()
        rate = rates.get((d, cur))
        if rate is None:
            continue
        correct = convert_minor(inv["total_minor"], cur, BASE_CURRENCY, rate)
        booked = ar[inv["invoice_id"]]["debit_minor"]
        if abs(booked - correct) > tolerance:
            out.append({"invoice_id": inv["invoice_id"], "doc_id": inv["doc_id"],
                        "ar_row_id": ar[inv["invoice_id"]]["row_id"],
                        "rev_row_id": rev.get(inv["invoice_id"], {}).get("row_id"),
                        "fx_key": f"{d}|{cur}|{BASE_CURRENCY}",
                        "booked": booked, "correct": correct, "diff": abs(booked - correct)})
    return out


def unallocated_payments(payments, ledger):
    """Receipts credited to the unallocated-receipts account instead of AR."""
    docs = {p["payment_id"]: p for p in payments}
    out = []
    for r in sorted(ledger, key=lambda r: r["row_id"]):
        if r["account"] == SUSPENSE and r["credit_minor"] > 0 and r["payment_id"] in docs:
            out.append({"payment_id": r["payment_id"], "doc_id": docs[r["payment_id"]]["doc_id"],
                        "row_id": r["row_id"], "amount": r["credit_minor"]})
    return out


def period_violations(invoices, ledger, manifest, tz="manifest"):
    """Invoices booked in this period's ledger although issued outside the period (local dates)."""
    tz = manifest["timezone"] if tz == "manifest" else tz
    ps, pe = date.fromisoformat(manifest["period_start"]), date.fromisoformat(manifest["period_end"])
    ar, rev = invoice_rows(ledger)
    out = []
    for inv in sorted(invoices, key=lambda i: i["invoice_id"]):
        d = local_date(inv["issued_at"], tz)
        if (d < ps or d > pe) and inv["invoice_id"] in rev:
            r = rev[inv["invoice_id"]]
            out.append({"invoice_id": inv["invoice_id"], "doc_id": inv["doc_id"],
                        "rev_row_id": r["row_id"], "ar_row_id": ar.get(inv["invoice_id"], {}).get("row_id"),
                        "amount": r["credit_minor"]})
    return out


def detect_all(manifest, invoices, payments, ledger, fx, naive=False):
    """Findings a careful reconciler can derive from the open records alone.

    naive=True ignores timezone offsets (diagnostic baseline only).
    """
    tz = None if naive else manifest["timezone"]
    out = []
    for g in duplicate_groups(invoices, tz):
        for other in g[1:]:
            out.append({"type": "duplicate_invoice", "affected_ids": [g[0]["invoice_id"], other["invoice_id"]],
                        "claimed_amount_minor": other["total_minor"], "currency": other["currency"],
                        "evidence_ids": [g[0]["doc_id"], other["doc_id"]],
                        "explanation": ("Both entries carry the same primary invoice number for this entity."
                                        if "source_invoice_number" in other else
                                        "Same entity, currency, total and local issue date.")})
    for m in fx_mismatches(invoices, ledger, fx, tz):
        out.append({"type": "fx_error", "affected_ids": [m["invoice_id"]],
                    "claimed_amount_minor": m["diff"], "currency": BASE_CURRENCY,
                    "evidence_ids": [m["doc_id"], m["ar_row_id"], m["fx_key"]],
                    "explanation": f"AR booked {m['booked']}, policy rate gives {m['correct']}."})
    for u in unallocated_payments(payments, ledger):
        out.append({"type": "payment_allocation", "affected_ids": [u["payment_id"]],
                    "claimed_amount_minor": u["amount"], "currency": BASE_CURRENCY,
                    "evidence_ids": [u["doc_id"], u["row_id"]],
                    "explanation": "Receipt not allocated to the invoice in AR."})
    for v in period_violations(invoices, ledger, manifest, tz):
        out.append({"type": "period_error", "affected_ids": [v["invoice_id"]],
                    "claimed_amount_minor": v["amount"], "currency": BASE_CURRENCY,
                    "evidence_ids": [v["doc_id"], v["rev_row_id"]],
                    "explanation": "Issued outside the period in company time, booked inside it."})
    return out
