"""Decisions across the whole collection (spec sections 32, 37).

``evaluate_card`` costs about 20ms, which is nothing for one card and nine
seconds for four hundred. So this lives behind its own endpoint rather than
inside the dashboard summary: the page loads immediately and the analysis
arrives when it arrives, instead of the whole dashboard waiting on it.

Only cards that can actually be decided are evaluated — a card with no
condition assessment or no graded comparables has no decision to compute, and
running the engine over it would burn time to produce a `insufficient_data` we
already know about. The ones skipped are counted and reported, so "expected
profit £2,140" is always read next to "across 31 of your 214 cards".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import BlockStatus, Decision
from app.models import Card, ConditionAssessment, MarketPrice
from app.money import to_minor
from app.services import evaluation

__all__ = ["CollectionDecisions", "Opportunity", "analyse_collection"]

#: Above this many analysable cards the sweep is truncated rather than left to
#: run for a minute. The cut is reported, never silent.
DEFAULT_LIMIT = 300


@dataclass
class Opportunity:
    """One card's verdict, flattened for a ranked list."""

    card_id: str
    name: str
    set_label: str | None
    decision: str
    headline: str
    confidence: str
    company_code: str | None = None
    tier_name: str | None = None
    expected_profit: float | None = None
    roi_pct: float | None = None
    probability_of_profit: float | None = None
    opportunity_score: float | None = None
    grading_cost: float | None = None
    net_raw_alternative: float | None = None
    #: Share of the likely grades with sales behind them. Below 1.0 the profit
    #: and ROI above are conditional, and a ranked list must show that.
    coverage: float = 0.0
    is_user_override: bool = False


@dataclass
class CollectionDecisions:
    currency: str = "GBP"
    analysed: int = 0
    total_cards: int = 0
    skipped_not_ready: int = 0
    truncated: bool = False
    batch_size: int = 1

    expected_profit: float | None = None
    potential_graded_value: float | None = None
    potential_uplift: float | None = None
    total_grading_cost: float | None = None

    counts: dict[str, int] = field(default_factory=dict)
    opportunities: list[Opportunity] = field(default_factory=list)
    status: str = BlockStatus.OK.value
    reason: str | None = None


def _analysable(db: Session) -> list[Card]:
    """Cards with both a current assessment and a computed price.

    Both are needed: the grade distribution comes from the assessment and the
    outcome values from the prices. Without either there is nothing to expect,
    so evaluating them would only produce a status we can infer for free.
    """
    assessed = select(ConditionAssessment.card_id).where(ConditionAssessment.is_current.is_(True))
    priced = select(MarketPrice.catalog_key)
    return list(
        db.scalars(
            select(Card)
            .where(Card.id.in_(assessed), Card.catalog_key.in_(priced))
            .order_by(Card.updated_at.desc())
        )
    )


def analyse_collection(
    db: Session,
    *,
    batch_size: int = 1,
    limit: int = DEFAULT_LIMIT,
) -> CollectionDecisions:
    """Run the decision engine over every card that has enough behind it."""
    total_cards = db.scalar(select(func.count()).select_from(Card)) or 0
    candidates = _analysable(db)

    result = CollectionDecisions(total_cards=total_cards, batch_size=batch_size)
    result.skipped_not_ready = total_cards - len(candidates)
    if len(candidates) > limit:
        result.truncated = True
        candidates = candidates[:limit]

    profit_minor = 0
    graded_minor = 0
    raw_minor = 0
    cost_minor = 0
    counted = 0

    for card in candidates:
        evaluated = evaluation.evaluate_card(db, card, batch_size=batch_size)
        recommendation = evaluated.recommendation
        result.currency = evaluated.currency
        result.counts[recommendation.decision] = (
            result.counts.get(recommendation.decision, 0) + 1
        )
        result.analysed += 1

        result.opportunities.append(
            Opportunity(
                card_id=card.id,
                name=evaluated.raw.display_name,
                set_label=evaluated.raw.set_label,
                decision=recommendation.decision,
                headline=recommendation.headline,
                confidence=recommendation.confidence,
                company_code=recommendation.company_code,
                tier_name=recommendation.tier_name,
                expected_profit=recommendation.expected_profit,
                roi_pct=recommendation.roi_pct,
                probability_of_profit=recommendation.probability_of_profit,
                opportunity_score=recommendation.opportunity_score,
                grading_cost=recommendation.grading_cost,
                net_raw_alternative=recommendation.net_raw_alternative,
                coverage=recommendation.coverage,
                is_user_override=recommendation.is_user_override,
            )
        )

        # Only cards the engine would actually grade contribute to the totals.
        # Summing the expected profit of cards it told you *not* to grade would
        # describe a plan nobody is going to carry out.
        if recommendation.decision not in _GRADING_DECISIONS:
            continue
        if recommendation.expected_profit is None:
            continue
        quantity = max(1, card.quantity)
        profit_minor += (to_minor(recommendation.expected_profit) or 0) * quantity
        graded_minor += (to_minor(recommendation.expected_net) or 0) * quantity
        raw_minor += (to_minor(recommendation.net_raw_alternative) or 0) * quantity
        cost_minor += (to_minor(recommendation.grading_cost) or 0) * quantity
        counted += 1

    result.opportunities.sort(
        key=lambda item: (item.opportunity_score or -1, item.expected_profit or 0), reverse=True
    )

    if counted:
        result.expected_profit = round(profit_minor / 100, 2)
        result.potential_graded_value = round(graded_minor / 100, 2)
        result.potential_uplift = round((graded_minor - raw_minor) / 100, 2)
        result.total_grading_cost = round(cost_minor / 100, 2)

    if not candidates:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = (
            "No card has both a condition assessment and comparable sales yet, so there is "
            "nothing to decide. Assess a card and add its sales."
        )
    elif result.truncated:
        result.status = BlockStatus.PARTIAL.value
        result.reason = (
            f"Analysed the {limit} most recently updated ready cards. "
            "Raise the limit to include the rest."
        )
    elif result.skipped_not_ready:
        result.status = BlockStatus.PARTIAL.value
        result.reason = (
            f"{result.skipped_not_ready} of {total_cards} cards were skipped: they need a "
            "condition assessment and comparable sales before they can be decided."
        )
    return result


#: Decisions that mean money would actually be spent on grading.
_GRADING_DECISIONS = {Decision.GRADE.value, Decision.GRADE_IF_BATCH_FILLED.value}
