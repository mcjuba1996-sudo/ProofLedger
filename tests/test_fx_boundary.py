"""A clean foreign-currency invoice really crosses the printed period boundary."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import pytest

from proofledger.generator import generate_episode


def has_correct_fx_boundary(episode):
    manifest = episode["manifest"]
    start, end = manifest["period_start"], manifest["period_end"]
    base = manifest["base_currency"]
    zone = datetime.fromisoformat(start + "T00:00:00" + manifest["timezone"]).tzinfo
    rates = {(r["date"], r["source_currency"]): Decimal(r["rate"]) for r in episode["fx"]
             if r["target_currency"] == base}
    exponents = manifest["rounding"]["minor_unit_exponent"]
    for inv in episode["invoices"]:
        issued = datetime.fromisoformat(inv["issued_at"])
        local = issued.astimezone(zone).date().isoformat()
        printed = issued.date().isoformat()
        currency = inv["currency"]
        if currency == base or start <= printed <= end or not start <= local <= end:
            continue
        minor = Decimal(inv["total_minor"]) * Decimal(10) ** (exponents[base] - exponents[currency])
        booked = (minor * rates[local, currency]).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        naive = (minor * rates[printed, currency]).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        rows = [r for r in episode["ledger"] if r["invoice_id"] == inv["invoice_id"] and r["payment_id"] is None]
        posting = sorted((r["account"], r["debit_minor"], r["credit_minor"], r["currency"]) for r in rows)
        if naive != booked and booked > 0 and posting == [("1210", booked, 0, base), ("6010", 0, booked, base)]:
            assert all(datetime.fromisoformat(r["timestamp"]).astimezone(zone).date().isoformat() == local
                       for r in rows)
            return True
    return False


@pytest.mark.parametrize("seed,count,entities", [
    (126, 10, 8), (128, 10, 8), (138, 10, 8), (16105, 10, 8),
    (0, 1, 2), (4, 1, 2), (5, 1, 2), (128, 1, 2),
])
def test_clean_business_day_and_fx_postings(seed, count, entities):
    episode = generate_episode(seed, {"boundary_distractors": count, "n_entities": entities})
    assert episode["truth"]["issues"] == []
    assert has_correct_fx_boundary(episode)


def test_single_timezone_stays_single_with_boundary_distractors():
    episode = generate_episode(128, {"boundary_distractors": 1, "mixed_timezones": False})
    assert all(inv["issued_at"].endswith(episode["manifest"]["timezone"]) for inv in episode["invoices"])
