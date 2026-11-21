"""Money helpers. All amounts are integers in minor units; rates are decimal strings."""
from decimal import Decimal, ROUND_HALF_UP

BASE_CURRENCY = "KZT"
EXP = {"KZT": 2, "USD": 2, "EUR": 2}  # minor-unit exponent per currency


def round_half_up(d: Decimal) -> int:
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def convert_minor(amount_minor: int, src: str, dst: str, rate) -> int:
    """Convert minor units of src into minor units of dst.

    rate: 1 major unit of src = rate major units of dst (Decimal or str).
    Same currency returns the amount unchanged. Rounding: half up to a minor unit.
    """
    if src == dst:
        return int(amount_minor)
    major = Decimal(amount_minor) / (Decimal(10) ** EXP[src])
    return round_half_up(major * Decimal(rate) * (Decimal(10) ** EXP[dst]))
