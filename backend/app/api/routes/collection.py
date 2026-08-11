"""Collection-level endpoints: dashboard summary and filter facets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from app.api.deps import DbSession
from app.models import Card
from app.schemas.common import ApiModel
from app.services import cards_service, collection_service, portfolio
from app.services.collection_service import CollectionSummary

router = APIRouter(prefix="/collection", tags=["collection"])


class Facets(ApiModel):
    """Distinct values actually present in the collection, for filter menus."""

    sets: list[str]
    languages: list[str]
    variants: list[str]
    rarities: list[str]
    statuses: list[str]


@router.get("/summary", response_model=CollectionSummary, summary="Dashboard summary")
def summary(db: DbSession) -> CollectionSummary:
    return collection_service.build_summary(db)


@router.get("/facets", response_model=Facets, summary="Filter options present in the collection")
def facets(db: DbSession) -> Facets:
    return Facets(
        sets=cards_service.distinct_values(db, Card.set_code),
        languages=cards_service.distinct_values(db, Card.language),
        variants=cards_service.distinct_values(db, Card.variant),
        rarities=cards_service.distinct_values(db, Card.rarity),
        statuses=cards_service.distinct_values(db, Card.status),
    )


class OpportunityOut(ApiModel):
    """One card's verdict, flattened for a ranked list."""

    card_id: str
    name: str
    set_label: str | None = None
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
    coverage: float = 0.0
    is_user_override: bool = False
    liquidity_score: float | None = None
    liquidity_band: str | None = None
    trend_direction: str | None = None


class CollectionDecisionsOut(ApiModel):
    """What the decision engine makes of the collection as a whole."""

    status: str
    reason: str | None = None
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
    counts: dict[str, int] = Field(default_factory=dict)
    opportunities: list[OpportunityOut] = Field(default_factory=list)


@router.get(
    "/decisions",
    response_model=CollectionDecisionsOut,
    summary="Run the decision engine across the collection",
    description=(
        "Separate from `/summary` on purpose: evaluating a card costs about 20ms, so a large "
        "collection would keep the dashboard waiting. The page loads first and this arrives "
        "after.\n\n"
        "Only cards with both a condition assessment and comparable sales are analysed — the "
        "rest have nothing to decide. The number skipped is always reported, so a total is "
        "never read without knowing how much of the collection it covers.\n\n"
        "The money totals count only cards the engine would actually grade. Summing the "
        "expected profit of cards it told you not to grade would describe a plan nobody is "
        "going to carry out."
    ),
)
def decisions(
    db: DbSession,
    batch_size: Annotated[
        int, Query(ge=1, le=1000, description="Cards assumed to share one submission.")
    ] = 1,
    limit: Annotated[int, Query(ge=1, le=2000)] = portfolio.DEFAULT_LIMIT,
) -> CollectionDecisionsOut:
    result = portfolio.analyse_collection(db, batch_size=batch_size, limit=limit)
    return CollectionDecisionsOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        analysed=result.analysed,
        total_cards=result.total_cards,
        skipped_not_ready=result.skipped_not_ready,
        truncated=result.truncated,
        batch_size=result.batch_size,
        expected_profit=result.expected_profit,
        potential_graded_value=result.potential_graded_value,
        potential_uplift=result.potential_uplift,
        total_grading_cost=result.total_grading_cost,
        counts=result.counts,
        opportunities=[OpportunityOut(**vars(item)) for item in result.opportunities],
    )
