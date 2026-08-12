"""What a card actually fetched, and whether the engine was right about it.

Everything else in this application is a projection: what a card is worth, what
grading would cost, what a sale would net. This module records the one figure
that is not — the money that actually arrived — and then scores the projections
against it.

That gap mattered. ``prediction_results`` has scored *grade* predictions since
Phase 8, so the app could tell you it called a PSA 9 correctly while having no
idea whether the submission made or lost money. In a build whose first principle
is **realisable profit rather than theoretical value**, being unable to check the
profit half is the wrong thing to be missing.

Three rules shape it.

**Realised beats derived, and the two are kept apart.** Fees can be computed
from the selling profile, and are, so recording a sale is a price and a date
rather than a form of nine boxes. But when a payout statement gives one number,
that number is the truth and the estimate is not — so a user-entered net is
stored as such and never quietly recomputed.

**Null grading cost means unrecorded, not free.** A realised profit computed
without it would flatter grading, which is the exact bias this application
exists to correct, so it is reported as incomplete instead.

**A sale is scored against what was known then, not now.** The comparison worth
making is against the market's view on the day you sold — which ``price_snapshots``
has been accruing since Phase 3 — not against today's price, which has moved for
reasons that have nothing to do with your decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import CardStatus
from app.models import Card, CardDisposal, PriceSnapshot
from app.money import to_major
from app.services import economics

__all__ = [
    "DisposalOutcome",
    "RealisedReport",
    "net_from_components",
    "realised",
    "record_disposal",
    "score",
]


def net_from_components(
    *,
    gross_minor: int,
    shipping_income_minor: int | None,
    fees_minor: int | None,
    postage_cost_minor: int | None,
    packaging_cost_minor: int | None,
) -> int:
    """Money in, minus everything that left. Unstated parts count as zero here.

    Deliberately not the same as treating them as unknown: the caller decides
    whether to supply an estimate from the selling profile, and if it chooses
    not to, the arithmetic is over what it was actually told.
    """
    return (
        gross_minor
        + (shipping_income_minor or 0)
        - (fees_minor or 0)
        - (postage_cost_minor or 0)
        - (packaging_cost_minor or 0)
    )


def estimate_costs(
    db: Session,
    *,
    gross_minor: int,
    graded: bool,
    profile_code: str | None = None,
) -> dict[str, int] | None:
    """What the selling profile says this sale would have cost.

    Used to pre-fill, never to overwrite. A sale recorded with its real payout
    keeps that payout.
    """
    profile = economics.profile_by_code(db, profile_code) or economics.default_profile(db)
    net = economics.net_sale_value(gross_minor, profile, graded=graded)
    if net is None:
        return None
    return {
        "shipping_income_minor": net.shipping_income_minor,
        # One figure, because a payout statement gives a total far more readily
        # than a breakdown, and three separate estimates read as three facts.
        "fees_minor": (
            net.platform_fee_minor + net.payment_fee_minor + net.other_fee_minor
            + net.listing_fee_minor
        ),
        "postage_cost_minor": net.postage_cost_minor,
        "packaging_cost_minor": net.packaging_cost_minor,
    }


def record_disposal(
    db: Session,
    card: Card,
    *,
    sold_on: date,
    gross_minor: int,
    sold_graded: bool = False,
    grade_label: str = "raw",
    grade: float | None = None,
    company_id: str | None = None,
    platform: str | None = None,
    shipping_income_minor: int | None = None,
    fees_minor: int | None = None,
    postage_cost_minor: int | None = None,
    packaging_cost_minor: int | None = None,
    net_proceeds_minor: int | None = None,
    grading_cost_minor: int | None = None,
    notes: str | None = None,
    currency: str = "GBP",
) -> CardDisposal:
    """Record a sale and mark the card sold.

    Anything not supplied is estimated from the selling profile so that the
    common case is a price and a date. A supplied ``net_proceeds_minor`` wins
    over all of it and is flagged as yours.
    """
    estimated = estimate_costs(
        db, gross_minor=gross_minor, graded=sold_graded, profile_code=platform
    ) or {}

    shipping_income = (
        shipping_income_minor
        if shipping_income_minor is not None
        else estimated.get("shipping_income_minor")
    )
    fees = fees_minor if fees_minor is not None else estimated.get("fees_minor")
    postage = (
        postage_cost_minor if postage_cost_minor is not None else estimated.get("postage_cost_minor")
    )
    packaging = (
        packaging_cost_minor
        if packaging_cost_minor is not None
        else estimated.get("packaging_cost_minor")
    )

    derived = net_from_components(
        gross_minor=gross_minor,
        shipping_income_minor=shipping_income,
        fees_minor=fees,
        postage_cost_minor=postage,
        packaging_cost_minor=packaging,
    )

    disposal = CardDisposal(
        card_id=card.id,
        catalog_key=card.catalog_key,
        card_name=card.name,
        sold_on=sold_on,
        platform=platform,
        currency=currency,
        sold_graded=sold_graded,
        company_id=company_id,
        grade=grade,
        grade_label=grade_label,
        gross_minor=gross_minor,
        shipping_income_minor=shipping_income,
        fees_minor=fees,
        postage_cost_minor=postage,
        packaging_cost_minor=packaging,
        net_proceeds_minor=net_proceeds_minor if net_proceeds_minor is not None else derived,
        net_is_user_entered=net_proceeds_minor is not None,
        grading_cost_minor=grading_cost_minor,
        notes=notes,
    )
    db.add(disposal)
    card.status = CardStatus.SOLD.value
    db.flush()
    return disposal


# --- Scoring ------------------------------------------------------------------


@dataclass
class DisposalOutcome:
    """One closed position, and what it says about the decision that led to it."""

    disposal_id: str
    card_id: str | None
    name: str
    sold_on: date
    grade_label: str
    sold_graded: bool
    currency: str = "GBP"

    net_proceeds: float | None = None
    purchase_price: float | None = None
    grading_cost: float | None = None
    #: Net proceeds less what you paid for it and what grading cost.
    realised_profit: float | None = None
    #: Null when a cost is unrecorded, because a profit missing a cost is not a
    #: profit. The reason says which one.
    profit_is_complete: bool = False

    #: What the market said the card was worth on the day it sold, from
    #: `price_snapshots` — the honest comparison, since today's price has moved
    #: for reasons that have nothing to do with this decision.
    market_value_on_the_day: float | None = None
    #: The *sale price* against that value, not the payout: both sides gross, so
    #: this measures how well the card sold rather than what the fees took.
    vs_market_pct: float | None = None

    #: For a slab: what the raw card was worth that day, so "was grading worth
    #: it" can be answered with two numbers that both actually happened.
    raw_value_on_the_day: float | None = None
    grading_gain: float | None = None

    reason: str | None = None


@dataclass
class RealisedReport:
    currency: str = "GBP"
    sold: int = 0
    #: Positions where every cost is known, so the profit is a real figure.
    scored: int = 0
    total_net_proceeds: float | None = None
    total_realised_profit: float | None = None
    #: Only across the positions that could be scored — never a total that
    #: quietly leaves out the ones missing a purchase price.
    graded_sales: int = 0
    raw_sales: int = 0
    total_grading_gain: float | None = None
    items: list[DisposalOutcome] = field(default_factory=list)
    status: str = "ok"
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


def _snapshot_on(
    db: Session, catalog_key: str | None, grade_label: str, on: date
) -> int | None:
    """The most recent stored value for this grade at or before a date."""
    if not catalog_key:
        return None
    row = db.scalars(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.catalog_key == catalog_key,
            PriceSnapshot.grade_label == grade_label,
            PriceSnapshot.snapshot_date <= on,
        )
        .order_by(PriceSnapshot.snapshot_date.desc())
    ).first()
    return row.value_minor if row else None


def _profit_minor(disposal: CardDisposal, card: Card | None) -> tuple[int | None, list[str]]:
    """Realised profit in minor units, or ``None`` and what is missing.

    Kept in minor units and shared with the totals rather than recomputed from
    the rounded major figure: money is integer pennies everywhere in this build
    precisely so a sum never drifts from its parts.
    """
    missing: list[str] = []
    if card is None or card.purchase_price_minor is None:
        missing.append("what you paid for it")
    if disposal.sold_graded and disposal.grading_cost_minor is None:
        missing.append("what grading cost")
    if missing:
        return None, missing
    spent = (card.purchase_price_minor or 0) + (disposal.grading_cost_minor or 0)
    return disposal.net_proceeds_minor - spent, []


def _grading_gain_minor(db: Session, disposal: CardDisposal) -> tuple[int | None, int | None]:
    """What the slab netted over the raw card on the same day, less the fee.

    Returns ``(raw_value_minor, gain_minor)``; either may be ``None`` when the
    history or the cost is missing.
    """
    if not disposal.sold_graded:
        return None, None
    raw_on_the_day = _snapshot_on(db, disposal.catalog_key, "raw", disposal.sold_on)
    if raw_on_the_day is None:
        return None, None
    if disposal.grading_cost_minor is None:
        return raw_on_the_day, None
    return (
        raw_on_the_day,
        disposal.net_proceeds_minor - raw_on_the_day - disposal.grading_cost_minor,
    )


def score(db: Session, disposal: CardDisposal) -> DisposalOutcome:
    """Turn one sale into what it says about the decision behind it."""
    card = db.get(Card, disposal.card_id) if disposal.card_id else None
    outcome = DisposalOutcome(
        disposal_id=disposal.id,
        card_id=disposal.card_id,
        name=disposal.card_name or (card.name if card else "Deleted card"),
        sold_on=disposal.sold_on,
        grade_label=disposal.grade_label,
        sold_graded=disposal.sold_graded,
        currency=disposal.currency,
        net_proceeds=to_major(disposal.net_proceeds_minor),
        purchase_price=to_major(card.purchase_price_minor) if card else None,
        grading_cost=to_major(disposal.grading_cost_minor),
    )

    profit, missing = _profit_minor(disposal, card)
    if missing:
        # A profit with a cost missing from it is not a profit, and reporting one
        # would flatter exactly the decision this application exists to test.
        outcome.reason = (
            f"Profit needs {' and '.join(missing)}, which is not recorded. "
            "The proceeds above are real; the profit would not be."
        )
    else:
        outcome.realised_profit = to_major(profit)
        outcome.profit_is_complete = True

    on_the_day = _snapshot_on(db, disposal.catalog_key, disposal.grade_label, disposal.sold_on)
    if on_the_day:
        outcome.market_value_on_the_day = to_major(on_the_day)
        # Gross against gross. A snapshot is a *sale price*, so measuring the
        # net payout against it reports the fee load as though it were selling
        # badly — every sale would read about a tenth under the market and the
        # number would say nothing about the sale at all.
        outcome.vs_market_pct = round((disposal.gross_minor - on_the_day) / on_the_day * 100, 1)

    raw_value, gain = _grading_gain_minor(db, disposal)
    outcome.raw_value_on_the_day = to_major(raw_value)
    outcome.grading_gain = to_major(gain)
    return outcome


def realised(db: Session, *, limit: int = 500) -> RealisedReport:
    """Every closed position, newest first."""
    rows = list(
        db.scalars(
            select(CardDisposal).order_by(CardDisposal.sold_on.desc()).limit(limit)
        )
    )
    report = RealisedReport(sold=len(rows))
    if not rows:
        report.status = "insufficient_data"
        report.reason = (
            "No sale has been recorded yet. Mark a card sold and this starts scoring the "
            "decisions behind them."
        )
        return report

    report.currency = rows[0].currency
    proceeds_minor = 0
    profit_minor = 0
    gain_minor = 0
    gains = 0

    for row in rows:
        report.items.append(score(db, row))
        proceeds_minor += row.net_proceeds_minor
        if row.sold_graded:
            report.graded_sales += 1
        else:
            report.raw_sales += 1

        # Summed from the same minor-unit arithmetic the rows were scored with,
        # never from the rounded figure on the way out.
        card = db.get(Card, row.card_id) if row.card_id else None
        profit, missing = _profit_minor(row, card)
        if not missing and profit is not None:
            report.scored += 1
            profit_minor += profit
        _, gain = _grading_gain_minor(db, row)
        if gain is not None:
            gain_minor += gain
            gains += 1

    report.total_net_proceeds = to_major(proceeds_minor)
    report.total_realised_profit = to_major(profit_minor) if report.scored else None
    report.total_grading_gain = to_major(gain_minor) if gains else None

    unscored = report.sold - report.scored
    if unscored:
        report.notes.append(
            f"{unscored} of {report.sold} sale(s) are missing a purchase price or a grading cost, "
            "so they are counted in the proceeds and left out of the profit. A total that "
            "silently dropped a cost would be the wrong number in the flattering direction."
        )
    if report.graded_sales and report.total_grading_gain is None:
        report.notes.append(
            "No graded sale could be compared against the raw card, which needs a stored price "
            "for the day it sold. Price history accrues from the day a card is first valued."
        )
    return report
