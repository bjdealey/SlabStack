"""Cuts across the collection, and a look back at what actually happened.

Everything here is a *view*. The decision engine says whether a card is worth
grading, the market engine says what it is worth, the submission engine says
what a parcel cost — analytics ranks, filters and compares those answers, and
never re-derives one of them.

That rule matters more than it sounds. The obvious way to build a "high upside"
filter is to write a fresh definition of upside; the obvious way to build a
selling queue is to invent a listing price. Both give you two numbers for one
question, and the second one is always the one that goes stale. So every figure
below is read from an engine that already produced it, and the filters are
predicates over those figures rather than opinions of their own.

Three things this adds that nothing else does:

**A selling queue.** The decision engine says "sell raw" and moves on. What it
never says is what to ask for it, which is the next question you actually have.

**Submission ROI.** What a parcel was predicted to be worth against what it
turned out to be worth, once the grades are back. Nothing else compares a
prediction to reality, and Phase 8 learns from exactly this comparison.

**One-click cuts.** The same verdicts, sliced by the question you are asking
today — what should I send, what should I list, what is quietly dying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import BlockStatus, Confidence, Decision, SubmissionStatus, TrendDirection
from app.models import Card, GradingSubmission, MarketPrice
from app.money import to_major, to_minor
from app.services import (
    decision as decision_engine,
)
from app.services import (
    economics,
    market_service,
    portfolio,
    settings_service,
    submissions,
)

#: Directions that mean the price is going the wrong way. `insufficient_data` is
#: deliberately absent: not knowing is not the same as falling.
_FALLING = {TrendDirection.DOWN.value, TrendDirection.STRONG_DOWN.value}

__all__ = [
    "FILTERS",
    "CollectionFilter",
    "FilterResult",
    "Opportunities",
    "SellingQueue",
    "SubmissionReturns",
    "filter_collection",
    "opportunities",
    "selling_queue",
    "submission_returns",
]


# --- Ranked opportunities ----------------------------------------------------


@dataclass
class Opportunities:
    """The decision engine's verdicts, cut down to the ones you can act on.

    Deliberately the *same* computation as ``/collection/decisions`` rather than
    a second one — two rankings of the same question would eventually disagree,
    and the loser would be whichever the user happened not to be looking at.
    """

    currency: str = "GBP"
    analysed: int = 0
    total_cards: int = 0
    actionable: int = 0
    expected_profit: float | None = None
    total_grading_cost: float | None = None
    items: list[portfolio.Opportunity] = field(default_factory=list)
    status: str = BlockStatus.OK.value
    reason: str | None = None


#: Verdicts that mean money would be spent on grading.
_ACTIONABLE = {Decision.GRADE.value, Decision.GRADE_IF_BATCH_FILLED.value}


def opportunities(
    db: Session, *, batch_size: int = 1, limit: int = portfolio.DEFAULT_LIMIT
) -> Opportunities:
    """Cards worth grading, best first."""
    decisions = portfolio.analyse_collection(db, batch_size=batch_size, limit=limit)
    actionable = [item for item in decisions.opportunities if item.decision in _ACTIONABLE]

    return Opportunities(
        currency=decisions.currency,
        analysed=decisions.analysed,
        total_cards=decisions.total_cards,
        actionable=len(actionable),
        expected_profit=decisions.expected_profit,
        total_grading_cost=decisions.total_grading_cost,
        items=actionable,
        status=decisions.status,
        reason=(
            decisions.reason
            if not actionable
            else (
                f"{len(actionable)} of {decisions.analysed} analysed card(s) are worth grading. "
                + (decisions.reason or "")
            ).strip()
        ),
    )


# --- The raw selling queue ---------------------------------------------------


@dataclass
class SellingCandidate:
    """One card to sell raw, and what to ask for it."""

    card_id: str
    name: str
    set_label: str | None = None
    decision: str = Decision.SELL_RAW.value

    #: What the market says it realistically sells for.
    realistic_sale: float | None = None
    #: What you would keep after fees and postage — the number that matters.
    net_proceeds: float | None = None
    #: What to list it at, and why that rather than the realistic figure.
    suggested_listing: float | None = None
    listing_basis: str | None = None

    liquidity_score: float | None = None
    liquidity_band: str | None = None
    days_since_last_sale: int | None = None
    trend_direction: str | None = None
    confidence: str = Confidence.NONE.value
    purchase_price: float | None = None
    #: Against what you paid, when you recorded it. Null when you did not.
    gain_vs_purchase: float | None = None
    blockers: list[str] = field(default_factory=list)


@dataclass
class SellingQueue:
    currency: str = "GBP"
    analysed: int = 0
    total_cards: int = 0
    #: Sum of what these would net, which is not the same as their headline value.
    total_net_proceeds: float | None = None
    items: list[SellingCandidate] = field(default_factory=list)
    status: str = BlockStatus.OK.value
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


#: Verdicts that mean the card is not going to a grader.
_SELLABLE = {Decision.SELL_RAW.value, Decision.KEEP_RAW.value, Decision.DO_NOT_GRADE.value}


def suggested_listing_minor(
    realistic_minor: int | None,
    high_quartile_minor: int | None,
    liquidity_score: float | None,
) -> tuple[int | None, str | None]:
    """What to *ask*, which is not what it will *fetch*.

    A listing price is a negotiating position: you list above the realistic sale
    and expect to come down. How far above is a liquidity question — a card that
    trades weekly can be listed near its median and still move, while one that
    trades twice a year needs room to be haggled down and time to find its buyer.

    The upper quartile of actual sales is the ceiling, because asking more than
    anyone has recently paid is how a listing sits unsold for a year. Where
    there is no quartile to cap against, the suggestion is a modest markup and
    says so.
    """
    if realistic_minor is None:
        return None, None

    # Illiquid cards need more negotiating room; liquid ones need less.
    if liquidity_score is None:
        markup = 0.10
        basis = "10% above the realistic sale price — no liquidity reading to judge by."
    elif liquidity_score >= 7:
        markup = 0.05
        basis = "5% above the realistic sale price. It trades often, so it does not need room."
    elif liquidity_score >= 4:
        markup = 0.10
        basis = "10% above the realistic sale price, leaving a little room to negotiate."
    else:
        markup = 0.18
        basis = (
            "18% above the realistic sale price. It trades rarely, so it needs room to be "
            "haggled down and time to find a buyer."
        )

    asking = round(realistic_minor * (1 + markup))

    # Never below the realistic sale price: the cap exists to stop you asking
    # more than the market pays, not to talk you into asking less than it does.
    # Where the quartile sits under the realistic figure — which happens when a
    # few recent sales pull the estimate up — there is nothing to cap.
    if high_quartile_minor and realistic_minor < high_quartile_minor < asking:
        asking = high_quartile_minor
        basis = (
            "Capped at the upper quartile of recent sales — asking more than anyone has "
            "recently paid is how a listing sits unsold."
        )
    return asking, basis


def selling_queue(db: Session, *, limit: int = portfolio.DEFAULT_LIMIT) -> SellingQueue:
    """Cards to sell raw, with a price to ask and what you would keep."""
    settings_values = settings_service.get_all(db)
    currency = settings_values.get("currency", "GBP")
    params = market_service.MarketParameters.from_settings(settings_values)

    decisions = portfolio.analyse_collection(db, batch_size=1, limit=limit)
    result = SellingQueue(
        currency=currency,
        analysed=decisions.analysed,
        total_cards=decisions.total_cards,
    )

    sellable = [item for item in decisions.opportunities if item.decision in _SELLABLE]
    if not sellable:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = (
            decisions.reason
            if not decisions.analysed
            else "Nothing in the analysed cards is better off sold raw right now."
        )
        return result

    net_total = 0
    counted = 0
    for item in sellable:
        card = db.get(Card, item.card_id)
        if card is None:  # pragma: no cover - ids come from the same sweep
            continue
        summary = market_service.summarise(db, card.catalog_key, params=params, currency=currency)
        raw = next((price for price in summary.prices if price.grade_label == "raw"), None)

        candidate = SellingCandidate(
            card_id=card.id,
            name=item.name,
            set_label=item.set_label,
            decision=item.decision,
            net_proceeds=item.net_raw_alternative,
            liquidity_score=summary.liquidity.score,
            liquidity_band=summary.liquidity.band,
            days_since_last_sale=summary.liquidity.days_since_last_sale,
            trend_direction=summary.trend.direction,
            confidence=raw.confidence if raw else Confidence.NONE.value,
            purchase_price=to_major(card.purchase_price_minor),
        )

        if raw is not None:
            candidate.realistic_sale = to_major(raw.realistic_sale_minor or raw.median_minor)
            asking, basis = suggested_listing_minor(
                raw.realistic_sale_minor or raw.median_minor,
                raw.high_quartile_minor,
                summary.liquidity.score,
            )
            candidate.suggested_listing = to_major(asking)
            candidate.listing_basis = basis
        else:
            candidate.blockers.append(
                "No raw sales stored, so there is nothing to price a listing against."
            )

        if candidate.net_proceeds is not None and card.purchase_price_minor is not None:
            candidate.gain_vs_purchase = round(
                candidate.net_proceeds - to_major(card.purchase_price_minor), 2
            )

        if candidate.net_proceeds is not None:
            net_total += to_minor(candidate.net_proceeds) or 0
            counted += 1
        result.items.append(candidate)

    # Most valuable first: this is a to-do list, and the biggest cheque is the
    # one worth writing the listing for tonight.
    result.items.sort(key=lambda item: item.net_proceeds or 0, reverse=True)
    if counted:
        result.total_net_proceeds = round(net_total / 100, 2)

    unpriced = [item for item in result.items if item.suggested_listing is None]
    if unpriced:
        result.status = BlockStatus.PARTIAL.value
        result.notes.append(
            f"{len(unpriced)} card(s) have no raw sales to price against, so no listing price "
            "is suggested for them."
        )
        result.reason = result.notes[0]
    return result


# --- Submission ROI ----------------------------------------------------------


@dataclass
class GradedCard:
    """One card that came back, and how the prediction held up."""

    card_id: str
    name: str
    predicted_grade: float | None = None
    actual_grade: float | None = None
    #: Positive when it graded better than predicted.
    surprise: float | None = None
    cost: float | None = None
    #: What the slab is worth now, from that grade's own market data.
    graded_value: float | None = None
    net_if_sold: float | None = None
    profit: float | None = None
    blockers: list[str] = field(default_factory=list)


@dataclass
class SubmissionReturn:
    submission_id: str
    reference: str
    company_code: str | None = None
    status: str = SubmissionStatus.DRAFT.value
    returned_at: str | None = None
    card_count: int = 0
    graded_count: int = 0

    total_cost: float | None = None
    total_value: float | None = None
    total_profit: float | None = None
    roi_pct: float | None = None

    #: Mean signed difference between actual and predicted grades. Positive
    #: means the grader was kinder than the model expected.
    mean_surprise: float | None = None
    cards: list[GradedCard] = field(default_factory=list)
    status_note: str | None = None


@dataclass
class SubmissionReturns:
    currency: str = "GBP"
    submissions: list[SubmissionReturn] = field(default_factory=list)
    scored: int = 0
    awaiting: int = 0
    total_cost: float | None = None
    total_profit: float | None = None
    roi_pct: float | None = None
    status: str = BlockStatus.OK.value
    reason: str | None = None


def submission_returns(db: Session) -> SubmissionReturns:
    """What the parcels you have sent actually returned.

    Only submissions that have come back can be scored. The rest are counted and
    reported rather than averaged in at zero, which would make every open
    submission look like a loss.
    """
    settings_values = settings_service.get_all(db)
    currency = settings_values.get("currency", "GBP")
    params = market_service.MarketParameters.from_settings(settings_values)
    profile = economics.default_profile(db)

    result = SubmissionReturns(currency=currency)
    rows = db.scalars(
        select(GradingSubmission).order_by(GradingSubmission.created_at.desc())
    ).all()

    cost_minor = 0
    profit_minor = 0
    for submission in rows:
        costing = submissions.cost_submission(db, submission)
        entry = SubmissionReturn(
            submission_id=submission.id,
            reference=submission.reference,
            company_code=costing.company_code,
            status=submission.status,
            returned_at=submission.returned_at.isoformat() if submission.returned_at else None,
            card_count=costing.card_count,
            total_cost=to_major(costing.total_minor),
        )

        graded_rows = [
            row for row in submission.cards if row.actual_grade is not None
        ]
        entry.graded_count = len(graded_rows)

        if not graded_rows:
            result.awaiting += 1
            entry.status_note = (
                "No grades recorded yet, so there is nothing to score. Record the grades when "
                "the parcel comes back."
            )
            result.submissions.append(entry)
            continue

        value_minor = 0
        surprises: list[float] = []
        entry_cost_minor = 0

        for row in graded_rows:
            card = db.get(Card, row.card_id)
            line = next(
                (item for item in costing.cards if item.submission_card_id == row.id), None
            )
            graded = GradedCard(
                card_id=row.card_id,
                name=line.name if line else (card.name if card else row.card_id),
                predicted_grade=row.predicted_grade,
                actual_grade=row.actual_grade,
                cost=to_major(line.total_minor) if line else None,
            )
            if row.predicted_grade is not None and row.actual_grade is not None:
                graded.surprise = round(row.actual_grade - row.predicted_grade, 2)
                surprises.append(graded.surprise)

            worth = _graded_value_minor(
                db, card, costing.company_code, row.actual_grade, params, currency
            )
            if worth is None:
                graded.blockers.append(
                    f"No {costing.company_code} sales stored at grade {row.actual_grade:g}, so "
                    "this slab cannot be valued."
                )
            else:
                graded.graded_value = to_major(worth)
                net = economics.net_sale_value(worth, profile, graded=True)
                if net is not None:
                    graded.net_if_sold = to_major(net.net_minor)
                    value_minor += net.net_minor
                    if line is not None:
                        graded.profit = round(
                            (net.net_minor - line.total_minor) / 100, 2
                        )

            if line is not None:
                entry_cost_minor += line.total_minor
            entry.cards.append(graded)

        entry.total_value = round(value_minor / 100, 2) if value_minor else None
        if entry_cost_minor and value_minor:
            entry.total_profit = round((value_minor - entry_cost_minor) / 100, 2)
            entry.roi_pct = round((value_minor - entry_cost_minor) / entry_cost_minor * 100, 1)
            cost_minor += entry_cost_minor
            profit_minor += value_minor - entry_cost_minor
        if surprises:
            entry.mean_surprise = round(sum(surprises) / len(surprises), 2)

        result.scored += 1
        result.submissions.append(entry)

    if cost_minor:
        result.total_cost = round(cost_minor / 100, 2)
        result.total_profit = round(profit_minor / 100, 2)
        result.roi_pct = round(profit_minor / cost_minor * 100, 1)

    if not rows:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = "No submissions yet, so there is nothing to score."
    elif not result.scored:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = (
            f"{result.awaiting} submission(s) are still out. Record the grades when they come "
            "back and this becomes a real return."
        )
    elif result.awaiting:
        result.status = BlockStatus.PARTIAL.value
        result.reason = (
            f"Scored {result.scored} returned submission(s); {result.awaiting} still out and "
            "not counted in any total."
        )
    return result


def _graded_value_minor(
    db: Session,
    card: Card | None,
    company_code: str | None,
    grade: float | None,
    params: market_service.MarketParameters,
    currency: str,
) -> int | None:
    """What this exact slab is worth, from that grade's own sales."""
    if card is None or company_code is None or grade is None:
        return None
    label = f"{company_code} {grade:g}"
    price = db.scalar(
        select(MarketPrice).where(
            MarketPrice.catalog_key == card.catalog_key,
            MarketPrice.grade_label == label,
        )
    )
    if price is None:
        return None
    return price.realistic_sale_minor or price.median_minor


# --- One-click filters -------------------------------------------------------


@dataclass
class CollectionFilter:
    """A named cut over the collection, defined in terms of engine output."""

    key: str
    label: str
    description: str


#: The cuts offered in the UI. Each is a predicate over figures the decision and
#: market engines already produced — never a fresh definition of the same idea.
FILTERS: tuple[CollectionFilter, ...] = (
    CollectionFilter("grade_now", "Grade now", "Clears your bar on its own today."),
    CollectionFilter(
        "grade_if_batch_filled",
        "Grade in a batch",
        "Worth grading, but only once a submission is full.",
    ),
    CollectionFilter("sell_raw", "Sell raw", "Better off sold as it is."),
    CollectionFilter("hold", "Hold", "Grading does not pay yet, but the market is rising."),
    CollectionFilter(
        "high_upside", "High upside", "The good outcomes are worth a lot more than the bad ones."
    ),
    CollectionFilter(
        "high_risk", "High risk", "A real chance of losing money against selling it raw."
    ),
    CollectionFilter(
        "low_liquidity",
        "Hard to sell",
        "Trades below the minimum liquidity you set, so any plan for it will be slow.",
    ),
    CollectionFilter(
        "declining", "Declining", "Prices are falling — waiting is costing you."
    ),
    CollectionFilter(
        "needs_data", "Needs data", "Cannot be decided until it has more behind it."
    ),
)

_FILTER_KEYS = {item.key for item in FILTERS}


@dataclass
class FilterResult:
    key: str
    label: str
    description: str
    currency: str = "GBP"
    matched: int = 0
    analysed: int = 0
    total_cards: int = 0
    #: Cards the engine could not decide, so could not be tested against this cut.
    unclassified: int = 0
    card_ids: list[str] = field(default_factory=list)
    items: list[portfolio.Opportunity] = field(default_factory=list)
    status: str = BlockStatus.OK.value
    reason: str | None = None


def filter_collection(
    db: Session, key: str, *, batch_size: int = 1, limit: int = portfolio.DEFAULT_LIMIT
) -> FilterResult:
    """Apply one named cut, and say what it could not classify."""
    if key not in _FILTER_KEYS:
        raise ValueError(f"'{key}' is not a collection filter.")
    definition = next(item for item in FILTERS if item.key == key)

    # "Hard to sell" means hard by *your* standard. Reusing the decision
    # engine's own bar keeps the filter and the verdicts in agreement rather
    # than inventing a second definition of illiquid.
    thresholds = decision_engine.Thresholds.from_settings(settings_service.get_all(db))

    decisions = portfolio.analyse_collection(db, batch_size=batch_size, limit=limit)
    result = FilterResult(
        key=key,
        label=definition.label,
        description=definition.description,
        currency=decisions.currency,
        analysed=decisions.analysed,
        total_cards=decisions.total_cards,
        # Cards the sweep never reached plus those it could not decide: both are
        # unanswered, and lumping them into "does not match" would be a lie.
        unclassified=decisions.skipped_not_ready
        + decisions.counts.get(Decision.INSUFFICIENT_DATA.value, 0),
    )

    matched = [item for item in decisions.opportunities if _matches(key, item, thresholds)]
    result.items = matched
    result.card_ids = [item.card_id for item in matched]
    result.matched = len(matched)

    if key == "needs_data":
        # This one is *about* the unanswered cards, so counting them as its own
        # blind spot would be nonsense.
        result.unclassified = 0

    if not decisions.analysed:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = decisions.reason
    elif result.unclassified:
        result.status = BlockStatus.PARTIAL.value
        result.reason = (
            f"{result.unclassified} card(s) could not be decided, so they were not tested "
            "against this filter."
        )
    return result


def _matches(key: str, item: portfolio.Opportunity, thresholds: decision_engine.Thresholds) -> bool:
    """Whether one card falls into a named cut.

    Every branch reads a figure the decision engine produced. Where a figure is
    unknown the card does **not** match: an unknown risk is not a low risk, and
    a card with no trend behind it is not a falling one.
    """
    if key == "grade_now":
        return item.decision == Decision.GRADE.value
    if key == "grade_if_batch_filled":
        return item.decision == Decision.GRADE_IF_BATCH_FILLED.value
    if key == "sell_raw":
        return item.decision == Decision.SELL_RAW.value
    if key == "hold":
        return item.decision == Decision.HOLD.value
    if key == "needs_data":
        return item.decision == Decision.INSUFFICIENT_DATA.value

    if key == "high_upside":
        # Judged against what selling it raw would net, so "high" means high
        # relative to the alternative rather than high in absolute pounds.
        if item.expected_profit is None or not item.net_raw_alternative:
            return False
        return item.expected_profit >= item.net_raw_alternative * 0.5

    if key == "high_risk":
        # Better than even odds is not risky; anything less, on a card the
        # engine still wants to grade, is.
        if item.probability_of_profit is None:
            return False
        return item.probability_of_profit < 0.5 and item.decision in _ACTIONABLE

    if key == "low_liquidity":
        # Against your own minimum, not a number invented here. No reading at
        # all is not a low reading — that card belongs in "needs data".
        if item.liquidity_score is None:
            return False
        return item.liquidity_score < thresholds.minimum_liquidity_score

    if key == "declining":
        return item.trend_direction in _FALLING

    return False  # pragma: no cover - guarded by _FILTER_KEYS


def review_due(db: Session, today: date | None = None) -> list[Card]:
    """Cards on hold whose recheck date has arrived (spec section 33)."""
    today = today or date.today()
    return list(
        db.scalars(
            select(Card).where(Card.review_after.is_not(None), Card.review_after <= today)
        )
    )
