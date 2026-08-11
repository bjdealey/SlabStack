"""Money handling.

Every monetary column in the database is an ``INTEGER`` count of *minor units*
(pence for GBP, cents for USD/EUR) and is suffixed ``_minor``. Currency is
stored alongside it. The API speaks *major units* (e.g. ``18.80``) because that
is what the UI renders, but no arithmetic is ever performed on those floats:
the decision engine works exclusively in integers so that expected-value sums
over hundreds of cards cannot drift.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Currencies whose minor unit is not 1/100 are not supported yet; every
# currency SlabStack ships with is two-decimal.
MINOR_UNIT_EXPONENT = 2
_MINOR_UNIT_FACTOR = Decimal(10) ** MINOR_UNIT_EXPONENT
_QUANTUM = Decimal(1).scaleb(-MINOR_UNIT_EXPONENT)

SUPPORTED_CURRENCIES: tuple[str, ...] = ("GBP", "USD", "EUR", "CAD", "AUD", "JPY")


def to_minor(value: float | int | str | Decimal | None) -> int | None:
    """Convert a major-unit amount (``18.80``) to minor units (``1880``)."""
    if value is None:
        return None
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((amount * _MINOR_UNIT_FACTOR).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_major(minor: int | None) -> float | None:
    """Convert minor units (``1880``) to a major-unit amount (``18.8``)."""
    if minor is None:
        return None
    return float((Decimal(minor) / _MINOR_UNIT_FACTOR).quantize(_QUANTUM, rounding=ROUND_HALF_UP))


def apply_pct(minor: int, pct: float) -> int:
    """Apply a percentage (``3.5`` meaning 3.5%) to a minor-unit amount."""
    result = (Decimal(minor) * Decimal(str(pct)) / Decimal(100)).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    return int(result)


def allocate(total_minor: int, weights: list[int]) -> list[int]:
    """Split ``total_minor`` across ``weights`` losing not a single penny.

    Used by the submission optimiser to allocate shared shipping/insurance
    across cards (equal or value-weighted). Remainder pennies go to the
    largest weights first, so the parts always sum back to the total.
    """
    if not weights:
        return []
    total_weight = sum(weights)
    if total_weight <= 0:
        base, remainder = divmod(total_minor, len(weights))
        return [base + (1 if i < remainder else 0) for i in range(len(weights))]

    raw = [Decimal(total_minor) * Decimal(w) / Decimal(total_weight) for w in weights]
    floors = [int(r.to_integral_value(rounding="ROUND_FLOOR")) for r in raw]
    remainder = total_minor - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def format_money(minor: int | None, currency: str = "GBP") -> str:
    """An em dash for ``None``: "not known" and "zero" mean different things.

    A negative amount puts a true minus sign *before* the currency symbol rather
    than a hyphen after it, matching what the UI renders — so a figure quoted
    inside an explanation string reads the same as the same figure in a panel
    beside it.
    """
    if minor is None:
        return "—"
    symbol = {"GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥"}.get(currency, "")
    major = to_major(minor)
    sign = "−" if major < 0 else ""
    magnitude = abs(major)
    return (
        f"{sign}{symbol}{magnitude:,.2f}" if symbol else f"{sign}{magnitude:,.2f} {currency}"
    )
