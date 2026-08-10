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
from app.models import Card, CardImage, ConditionAssessment, GradingTier, MarketSale
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
    cards_with_value = _scalar(
        db,
        select(func.count()).select_from(Card).where(
            (Card.user_raw_value_minor.is_not(None)) | (Card.purchase_price_minor.is_not(None))
        ),
    )
    # Best available raw value per card: the user's own number wins, purchase
    # price is the fallback. Market value replaces both in Phase 3.
    known_raw_minor = _scalar(
        db,
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(Card.user_raw_value_minor, Card.purchase_price_minor, 0)
                    * Card.quantity
                ),
                0,
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
    decisions = DecisionCounts(
        reason=(
            "Decisions shown are your own overrides. Engine-generated decisions need "
            "grade probabilities and market data."
        )
    )
    for override, count in override_rows:
        if hasattr(decisions, override):
            setattr(decisions, override, count)
    decisions.insufficient_data = max(
        total_cards - sum(count for _, count in override_rows), 0
    )

    market_sales = _scalar(db, select(func.count()).select_from(MarketSale))
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
            count=market_sales,
            total=max(total_cards, 1),
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
                "Raw value is your own figure or purchase price. Market valuation, graded "
                "upside and expected profit need the market-data and decision engines."
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
