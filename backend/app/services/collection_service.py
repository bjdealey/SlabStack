"""Collection-level aggregates for the dashboard (spec section 28).

Phase 1 reports what is knowable without market data: how much is here, how much
of it is ready to be analysed, and what is blocking the rest. Potential graded
value and expected profit stay ``null`` with a stated reason rather than
appearing as zero, because a zero would read as "no upside" instead of "not
calculated".
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import BlockStatus, CardStatus
from app.models import (
    Card,
    CardImage,
    ConditionAssessment,
    GradingTier,
    MarketPrice,
    MarketSale,
)
from app.money import to_major
from app.schemas.common import ApiModel
from app.services import settings_service


class CollectionTotals(ApiModel):
    cards: int
    copies: int
    distinct_sets: int
    with_images: int
    with_condition: int
    ready_to_analyse: int


class CollectionValues(ApiModel):
    currency: str
    purchase_total: float | None
    user_valued_total: float | None
    known_raw_value: float | None
    cards_with_value: int
    potential_graded_value: float | None = None
    potential_uplift: float | None = None
    expected_profit: float | None = None
    values_status: str = BlockStatus.PARTIAL.value
    values_reason: str | None = None


class DecisionCounts(ApiModel):
    grade: int = 0
    grade_if_batch_filled: int = 0
    sell_raw: int = 0
    keep_raw: int = 0
    hold: int = 0
    do_not_grade: int = 0
    insufficient_data: int = 0
    status: str = BlockStatus.INSUFFICIENT_DATA.value
    reason: str | None = None


class ReadinessItem(ApiModel):
    key: str
    label: str
    count: int
    total: int
    action: str


class CollectionSummary(ApiModel):
    totals: CollectionTotals
    values: CollectionValues
    decisions: DecisionCounts
    by_status: dict[str, int]
    by_set: list[dict]
    recent_additions: int
    review_due: int
    readiness: list[ReadinessItem]
    market_sales_stored: int
    priced_tiers_configured: int


def _scalar(db: Session, statement) -> int:
    return db.scalar(statement) or 0


def build_summary(db: Session) -> CollectionSummary:
    values = settings_service.get_all(db)
    currency = values.get("currency", "GBP")

    total_cards = _scalar(db, select(func.count()).select_from(Card))
    total_copies = _scalar(db, select(func.coalesce(func.sum(Card.quantity), 0)))
    distinct_sets = _scalar(
        db, select(func.count(func.distinct(Card.set_code))).where(Card.set_code.is_not(None))
    )

    cards_with_images = _scalar(
        db, select(func.count(func.distinct(CardImage.card_id))).select_from(CardImage)
    )
    cards_with_condition = _scalar(
        db,
        select(func.count(func.distinct(ConditionAssessment.card_id))).where(
            ConditionAssessment.is_current.is_(True)
        ),
    )

    purchase_total_minor = _scalar(
        db, select(func.coalesce(func.sum(Card.purchase_price_minor * Card.quantity), 0))
    )
    user_value_minor = _scalar(
        db, select(func.coalesce(func.sum(Card.user_raw_value_minor * Card.quantity), 0))
    )
    # Best available raw value per card, in order of how close each source is to
    # what the user would actually get: their own figure, then the market's
    # realistic-sale estimate, then the bare median, then what they paid.
    #
    # Purchase price is last for a reason — it is what the card *cost*, which
    # says nothing about what it is worth now. It is a floor to fall back on,
    # not a valuation.
    raw_prices = (
        select(
            MarketPrice.catalog_key.label("catalog_key"),
            func.coalesce(
                MarketPrice.user_value_minor,
                MarketPrice.realistic_sale_minor,
                MarketPrice.median_minor,
            ).label("value_minor"),
        )
        .where(MarketPrice.grade_label == "raw")
        .subquery()
    )
    best_value = func.coalesce(
        Card.user_raw_value_minor, raw_prices.c.value_minor, Card.purchase_price_minor
    )
    valued = select(Card, best_value.label("best_minor")).outerjoin(
        raw_prices, Card.catalog_key == raw_prices.c.catalog_key
    ).subquery()

    cards_with_value = _scalar(
        db, select(func.count()).select_from(valued).where(valued.c.best_minor.is_not(None))
    )
    known_raw_minor = _scalar(
        db,
        select(func.coalesce(func.sum(valued.c.best_minor * valued.c.quantity), 0)).select_from(
            valued
        ),
    )
    cards_market_valued = _scalar(
        db,
        select(func.count())
        .select_from(valued)
        .where(valued.c.user_raw_value_minor.is_(None), valued.c.best_minor.is_not(None))
        .where(
            valued.c.catalog_key.in_(
                select(MarketPrice.catalog_key).where(MarketPrice.grade_label == "raw")
            )
        ),
    )

    status_rows = db.execute(
        select(Card.status, func.count()).group_by(Card.status)
    ).all()
    by_status = {row[0]: row[1] for row in status_rows}
    for status in CardStatus.values():
        by_status.setdefault(status, 0)

    set_rows = db.execute(
        select(
            func.coalesce(Card.set_name, Card.set_code, "Unassigned").label("set_label"),
            func.count().label("cards"),
            func.coalesce(
                func.sum(func.coalesce(Card.user_raw_value_minor, Card.purchase_price_minor, 0)), 0
            ).label("value_minor"),
        )
        .group_by("set_label")
        .order_by(func.count().desc())
        .limit(12)
    ).all()
    by_set = [
        {"set": row.set_label, "cards": row.cards, "value": to_major(row.value_minor)}
        for row in set_rows
    ]

    week_ago = date.today() - timedelta(days=7)
    recent = _scalar(
        db, select(func.count()).select_from(Card).where(func.date(Card.created_at) >= week_ago)
    )
    review_due = _scalar(
        db,
        select(func.count())
        .select_from(Card)
        .where(Card.review_after.is_not(None), Card.review_after <= date.today()),
    )

    override_rows = db.execute(
        select(Card.decision_override, func.count())
        .where(Card.decision_override.is_not(None))
        .group_by(Card.decision_override)
    ).all()
    # Deliberately *not* the engine's verdicts: running it over every card takes
    # long enough to hold up the dashboard, so it lives at /collection/decisions
    # and loads separately. These are the decisions you made yourself.
    decisions = DecisionCounts(
        reason=(
            "Decisions you set yourself. The engine's own verdicts are analysed separately, "
            "because running it across the collection takes a moment."
        )
    )
    for override, count in override_rows:
        if hasattr(decisions, override):
            setattr(decisions, override, count)
    decisions.insufficient_data = max(
        total_cards - sum(count for _, count in override_rows), 0
    )

    market_sales = _scalar(
        db, select(func.count()).select_from(MarketSale).where(MarketSale.is_excluded.is_(False))
    )
    cards_with_sales = _scalar(
        db,
        select(func.count())
        .select_from(Card)
        .where(
            Card.catalog_key.in_(
                select(MarketSale.catalog_key).where(MarketSale.is_excluded.is_(False))
            )
        ),
    )
    priced_tiers = _scalar(
        db,
        select(func.count())
        .select_from(GradingTier)
        .where(GradingTier.active.is_(True), GradingTier.price_minor > 0),
    )

    ready = _scalar(
        db,
        select(func.count(func.distinct(ConditionAssessment.card_id))).where(
            ConditionAssessment.is_current.is_(True),
            ConditionAssessment.completeness >= 0.5,
        ),
    )

    readiness = [
        ReadinessItem(
            key="photographed",
            label="Photographed",
            count=cards_with_images,
            total=total_cards,
            action="Upload front and back images",
        ),
        ReadinessItem(
            key="assessed",
            label="Condition assessed",
            count=cards_with_condition,
            total=total_cards,
            action="Record centering and defects",
        ),
        ReadinessItem(
            key="valued",
            label="Raw value known",
            count=cards_with_value,
            total=total_cards,
            action="Add a purchase price or your own estimate",
        ),
        ReadinessItem(
            key="market_data",
            label="Comparable sales stored",
            # Cards covered, not sales counted: readiness is "how much of the
            # collection can be analysed", and 143 sales across two cards leaves
            # the other ten unanalysable.
            count=cards_with_sales,
            total=total_cards,
            action="Import or enter sold comparables",
        ),
    ]

    return CollectionSummary(
        totals=CollectionTotals(
            cards=total_cards,
            copies=total_copies,
            distinct_sets=distinct_sets,
            with_images=cards_with_images,
            with_condition=cards_with_condition,
            ready_to_analyse=ready,
        ),
        values=CollectionValues(
            currency=currency,
            purchase_total=to_major(purchase_total_minor),
            user_valued_total=to_major(user_value_minor),
            known_raw_value=to_major(known_raw_minor),
            cards_with_value=cards_with_value,
            values_status=BlockStatus.PARTIAL.value,
            values_reason=(
                f"Raw value uses your own figure where you set one, a market valuation for "
                f"{cards_market_valued} card(s), and the purchase price otherwise. Graded "
                "upside and expected profit are analysed separately, across the cards that "
                "have enough behind them to be decided."
                if cards_market_valued
                else (
                    "Raw value is your own figure or purchase price — no card has comparable "
                    "sales yet. Graded upside and expected profit are analysed separately, "
                    "across the cards that have enough behind them to be decided."
                )
            ),
        ),
        decisions=decisions,
        by_status=by_status,
        by_set=by_set,
        recent_additions=recent,
        review_due=review_due,
        readiness=readiness,
        market_sales_stored=market_sales,
        priced_tiers_configured=priced_tiers,
    )
