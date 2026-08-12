"""Analytics: ranked opportunities, a selling queue, returns, and saved cuts.

Every endpoint here is a projection of an answer some other engine already gave.
None of them computes a verdict, a value or a cost of its own — if a number
appears below, it came from the decision, market, economics or submission engine
and can be traced back to the card page that shows the same figure.

That is why these are thin. The interesting work happened upstream; this is the
part that decides which of those answers you are asked to look at today.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.api.deps import DbSession
from app.api.errors import ApiError
from app.api.routes.collection import OpportunityOut
from app.enums import Confidence
from app.schemas.common import ApiModel
from app.services import analytics, disposals, portfolio

router = APIRouter(prefix="/analytics", tags=["analytics"])


# --- Opportunities -----------------------------------------------------------


class OpportunitiesOut(ApiModel):
    """Cards worth grading, best first."""

    status: str
    reason: str | None = None
    currency: str = "GBP"
    analysed: int = 0
    total_cards: int = 0
    actionable: int = 0
    expected_profit: float | None = None
    total_grading_cost: float | None = None
    items: list[OpportunityOut] = Field(default_factory=list)


@router.get(
    "/opportunities",
    response_model=OpportunitiesOut,
    summary="Best grading opportunities",
    description=(
        "The same verdicts as `/collection/decisions`, cut down to the cards you would "
        "actually send and ranked by opportunity score.\n\n"
        "Deliberately not a second ranking: two rankings of one question drift apart, and the "
        "one that goes stale is whichever you were not looking at. Every figure here is the "
        "figure that endpoint returns.\n\n"
        "`batch_size` matters — a card that does not pay on its own often pays in a submission "
        "of twenty, so the list changes with the parcel you have in mind."
    ),
)
def opportunities(
    db: DbSession,
    batch_size: Annotated[
        int, Query(ge=1, le=1000, description="Cards assumed to share one submission.")
    ] = 1,
    limit: Annotated[int, Query(ge=1, le=2000)] = portfolio.DEFAULT_LIMIT,
) -> OpportunitiesOut:
    result = analytics.opportunities(db, batch_size=batch_size, limit=limit)
    return OpportunitiesOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        analysed=result.analysed,
        total_cards=result.total_cards,
        actionable=result.actionable,
        expected_profit=result.expected_profit,
        total_grading_cost=result.total_grading_cost,
        items=[OpportunityOut(**vars(item)) for item in result.items],
    )


# --- The selling queue -------------------------------------------------------


class SellingCandidateOut(ApiModel):
    """One card to sell raw, and what to ask for it."""

    card_id: str
    name: str
    set_label: str | None = None
    decision: str
    realistic_sale: float | None = Field(
        default=None, description="What the market says it realistically sells for."
    )
    net_proceeds: float | None = Field(
        default=None, description="What you would keep after fees and postage."
    )
    suggested_listing: float | None = Field(
        default=None, description="What to ask, which is not what it will fetch."
    )
    listing_basis: str | None = None
    liquidity_score: float | None = None
    liquidity_band: str | None = None
    days_since_last_sale: int | None = None
    trend_direction: str | None = None
    confidence: str
    purchase_price: float | None = None
    gain_vs_purchase: float | None = Field(
        default=None, description="Null when you never recorded what you paid — not zero."
    )
    blockers: list[str] = Field(default_factory=list)


class SellingQueueOut(ApiModel):
    status: str
    reason: str | None = None
    currency: str = "GBP"
    analysed: int = 0
    total_cards: int = 0
    total_net_proceeds: float | None = None
    items: list[SellingCandidateOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@router.get(
    "/selling-queue",
    response_model=SellingQueueOut,
    summary="Cards to sell raw, with a price to ask",
    description=(
        "The decision engine says 'sell raw' and stops. This answers the question that comes "
        "next: what to list it at.\n\n"
        "A listing price is a negotiating position, not a valuation. The markup over the "
        "realistic sale price scales with liquidity — a card that trades weekly needs little "
        "room, one that trades twice a year needs plenty — and is capped at the upper quartile "
        "of what people have actually paid, because asking more than that is how a listing sits "
        "unsold.\n\n"
        "`net_proceeds` is the same figure the card page shows, after fees and postage. A card "
        "with no raw sales behind it gets no suggested price and says why."
    ),
)
def selling_queue(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=2000)] = portfolio.DEFAULT_LIMIT,
) -> SellingQueueOut:
    result = analytics.selling_queue(db, limit=limit)
    return SellingQueueOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        analysed=result.analysed,
        total_cards=result.total_cards,
        total_net_proceeds=result.total_net_proceeds,
        items=[SellingCandidateOut(**vars(item)) for item in result.items],
        notes=result.notes,
    )


# --- What to assess first ----------------------------------------------------


class AssessmentCandidateOut(ApiModel):
    """One unassessed card, and the most grading could possibly gain."""

    card_id: str
    name: str
    set_label: str | None = None
    verdict: str = Field(description="`assess`, `skip` or `unknown`.")
    reason: str | None = None
    ceiling: float | None = Field(
        default=None,
        description=(
            "The most grading could add, if the card came back at the best-priced grade. "
            "An upper bound, not a forecast — an assessment can only bring it down."
        ),
    )
    ceiling_is_complete: bool = Field(
        default=False,
        description=(
            "False when the best *priced* grade sits below the top of that company's ladder, "
            "which makes the ceiling a bound over the priced grades only."
        ),
    )
    company_code: str | None = None
    tier_name: str | None = None
    grading_cost: float | None = None
    best_grade_label: str | None = None
    best_net: float | None = None
    net_raw_value: float | None = None
    liquidity_score: float | None = None
    liquidity_band: str | None = None
    confidence: str = Confidence.NONE.value


class AssessmentQueueOut(ApiModel):
    status: str
    reason: str | None = None
    currency: str = "GBP"
    analysed: int = 0
    total_cards: int = 0
    unpriced: int = 0
    worth_assessing: int = 0
    ruled_out: int = 0
    unknown: int = 0
    truncated: bool = False
    total_ceiling: float | None = None
    items: list[AssessmentCandidateOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@router.get(
    "/assessment-queue",
    response_model=AssessmentQueueOut,
    summary="Which unassessed cards are worth the five minutes",
    description=(
        "Importing four hundred cards takes a second; assessing four hundred does not. The "
        "decision engine cannot rank them — it needs an assessment to say anything at all — so "
        "this ranks on the one thing already known about every card: what the market pays for it "
        "raw, and what it pays for the same card in a slab.\n\n"
        "The measure is a **ceiling**, not a forecast. Take the best-netting grade that has sales "
        "behind it, subtract what the card already nets raw and what grading would cost, and that "
        "is the most grading could possibly add. A card whose ceiling is negative cannot be worth "
        "grading in any condition, so it is ruled out without ever being looked at.\n\n"
        "**A bound is only a bound over what is priced.** If the best grade with sales behind it "
        "is a 9, a 10 might be worth far more and a negative ceiling proves nothing — those come "
        "back `unknown`, never `skip`. Ceilings are computed within one company, because pairing "
        "one grader's fee with another's slab price describes a route that does not exist."
    ),
)
def assessment_queue(
    db: DbSession,
    batch_size: Annotated[int, Query(ge=1, le=1000)] = 1,
    limit: Annotated[int, Query(ge=1, le=2000)] = portfolio.DEFAULT_LIMIT,
) -> AssessmentQueueOut:
    result = analytics.assessment_queue(db, batch_size=batch_size, limit=limit)
    return AssessmentQueueOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        analysed=result.analysed,
        total_cards=result.total_cards,
        unpriced=result.unpriced,
        worth_assessing=result.worth_assessing,
        ruled_out=result.ruled_out,
        unknown=result.unknown,
        truncated=result.truncated,
        total_ceiling=result.total_ceiling,
        items=[AssessmentCandidateOut(**vars(item)) for item in result.items],
        notes=result.notes,
    )


# --- Submission returns ------------------------------------------------------


class GradedCardOut(ApiModel):
    """One card that came back, and how the prediction held up."""

    card_id: str
    name: str
    predicted_grade: float | None = None
    actual_grade: float | None = None
    surprise: float | None = Field(
        default=None, description="Positive when it graded better than predicted."
    )
    cost: float | None = None
    graded_value: float | None = Field(
        default=None, description="What the slab is worth now, from that grade's own sales."
    )
    net_if_sold: float | None = None
    profit: float | None = None
    blockers: list[str] = Field(default_factory=list)


class SubmissionReturnOut(ApiModel):
    submission_id: str
    reference: str
    company_code: str | None = None
    status: str
    returned_at: str | None = None
    card_count: int = 0
    graded_count: int = 0
    total_cost: float | None = None
    total_value: float | None = None
    total_profit: float | None = None
    roi_pct: float | None = None
    mean_surprise: float | None = Field(
        default=None,
        description=(
            "Mean signed difference between actual and predicted grades. Positive means the "
            "grader was kinder than the model expected."
        ),
    )
    cards: list[GradedCardOut] = Field(default_factory=list)
    status_note: str | None = None


class SubmissionReturnsOut(ApiModel):
    status: str
    reason: str | None = None
    currency: str = "GBP"
    scored: int = 0
    awaiting: int = 0
    total_cost: float | None = None
    total_profit: float | None = None
    roi_pct: float | None = None
    submissions: list[SubmissionReturnOut] = Field(default_factory=list)


@router.get(
    "/submission-returns",
    response_model=SubmissionReturnsOut,
    summary="What the parcels you sent actually returned",
    description=(
        "Predicted grades against the grades that came back, and cost against what the slabs "
        "are now worth.\n\n"
        "Only submissions with grades recorded are scored. Ones still out are counted and "
        "reported separately rather than averaged in at zero, which would make every open "
        "parcel look like a loss.\n\n"
        "A slab whose grade has no sales behind it cannot be valued, and says so instead of "
        "falling back to a neighbouring grade's price."
    ),
)
def submission_returns(db: DbSession) -> SubmissionReturnsOut:
    result = analytics.submission_returns(db)
    return SubmissionReturnsOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        scored=result.scored,
        awaiting=result.awaiting,
        total_cost=result.total_cost,
        total_profit=result.total_profit,
        roi_pct=result.roi_pct,
        submissions=[
            SubmissionReturnOut(
                **{key: value for key, value in vars(entry).items() if key != "cards"},
                cards=[GradedCardOut(**vars(card)) for card in entry.cards],
            )
            for entry in result.submissions
        ],
    )


# --- What you actually made --------------------------------------------------


class DisposalOutcomeOut(ApiModel):
    """One closed position, and what it says about the decision behind it."""

    disposal_id: str
    card_id: str | None = None
    name: str
    sold_on: date
    grade_label: str
    sold_graded: bool
    currency: str = "GBP"
    net_proceeds: float | None = None
    purchase_price: float | None = None
    grading_cost: float | None = None
    realised_profit: float | None = None
    profit_is_complete: bool = False
    market_value_on_the_day: float | None = Field(
        default=None,
        description=(
            "What the card was worth the day it sold, from `price_snapshots`. Today's price "
            "has moved for reasons that have nothing to do with this decision."
        ),
    )
    vs_market_pct: float | None = None
    raw_value_on_the_day: float | None = None
    grading_gain: float | None = Field(
        default=None,
        description="What the slab netted over the raw card that day, less what grading cost.",
    )
    reason: str | None = None


class RealisedOut(ApiModel):
    status: str
    reason: str | None = None
    currency: str = "GBP"
    sold: int = 0
    scored: int = 0
    graded_sales: int = 0
    raw_sales: int = 0
    total_net_proceeds: float | None = None
    total_realised_profit: float | None = None
    total_grading_gain: float | None = None
    items: list[DisposalOutcomeOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@router.get(
    "/realised",
    response_model=RealisedOut,
    summary="What you actually made, against what was predicted",
    description=(
        "Every other figure in this application is a projection. These are the ones that "
        "happened.\n\n"
        "`prediction_results` has scored *grade* predictions since Phase 8, so the app could "
        "tell you it called a PSA 9 correctly while having no idea whether the submission made "
        "money. This is the other half.\n\n"
        "**A profit missing a cost is not reported as a profit.** A sale without a recorded "
        "purchase price, or a graded sale without a recorded grading cost, is counted in the "
        "proceeds and left out of the profit — a total that silently dropped a cost would be "
        "wrong in the flattering direction.\n\n"
        "`market_value_on_the_day` comes from `price_snapshots`, not from today's price: the "
        "question is whether the decision was right when it was made."
    ),
)
def realised(db: DbSession, limit: Annotated[int, Query(ge=1, le=2000)] = 500) -> RealisedOut:
    result = disposals.realised(db, limit=limit)
    return RealisedOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        sold=result.sold,
        scored=result.scored,
        graded_sales=result.graded_sales,
        raw_sales=result.raw_sales,
        total_net_proceeds=result.total_net_proceeds,
        total_realised_profit=result.total_realised_profit,
        total_grading_gain=result.total_grading_gain,
        items=[DisposalOutcomeOut(**vars(item)) for item in result.items],
        notes=result.notes,
    )


# --- Filters -----------------------------------------------------------------


class CollectionFilterOut(ApiModel):
    """A named cut over the collection, defined in terms of engine output."""

    key: str
    label: str
    description: str


class FilterResultOut(ApiModel):
    key: str
    label: str
    description: str
    status: str
    reason: str | None = None
    currency: str = "GBP"
    matched: int = 0
    analysed: int = 0
    total_cards: int = 0
    unclassified: int = Field(
        default=0,
        description=(
            "Cards the engine could not decide, so could not be tested against this cut. "
            "Reported rather than counted as non-matches: unanswered is not 'no'."
        ),
    )
    card_ids: list[str] = Field(default_factory=list)
    items: list[OpportunityOut] = Field(default_factory=list)


@router.get(
    "/filters",
    response_model=list[CollectionFilterOut],
    summary="The saved cuts on offer",
)
def filters() -> list[CollectionFilterOut]:
    return [CollectionFilterOut(**vars(item)) for item in analytics.FILTERS]


@router.get(
    "/filters/{key}",
    response_model=FilterResultOut,
    summary="Apply one saved cut",
    description=(
        "Each filter is a predicate over figures the decision and market engines already "
        "produced — never a fresh definition of the same idea. 'Hard to sell' uses the minimum "
        "liquidity score you configured, so the filter and the verdicts agree.\n\n"
        "Where a figure is unknown the card does not match: an unknown risk is not a low risk. "
        "Those cards are counted in `unclassified` so a short list is never mistaken for a "
        "complete one."
    ),
)
def apply_filter(
    db: DbSession,
    key: str,
    batch_size: Annotated[int, Query(ge=1, le=1000)] = 1,
    limit: Annotated[int, Query(ge=1, le=2000)] = portfolio.DEFAULT_LIMIT,
) -> FilterResultOut:
    try:
        result = analytics.filter_collection(db, key, batch_size=batch_size, limit=limit)
    except ValueError as exc:
        # Not NotFoundError: its message is "X 'y' was not found", which reads as
        # a missing record. The filter set is fixed and known, so the honest
        # message is that this key is not one of them — and it lists them.
        raise ApiError(
            code="not_found",
            message=f"{exc} Available: {', '.join(item.key for item in analytics.FILTERS)}.",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"key": key, "available": [item.key for item in analytics.FILTERS]},
        ) from exc
    return FilterResultOut(
        key=result.key,
        label=result.label,
        description=result.description,
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        matched=result.matched,
        analysed=result.analysed,
        total_cards=result.total_cards,
        unclassified=result.unclassified,
        card_ids=result.card_ids,
        items=[OpportunityOut(**vars(item)) for item in result.items],
    )
