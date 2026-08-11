"""The ``evaluate_card`` envelope — the contract the whole UI renders.

Spec section 45: this application is a decision engine with a collection
database attached, not a collection tracker with grading bolted on. Everything
the user needs in order to answer "should I grade this?" arrives in one
response, and the React side only visualises it.

The shape is fixed now, in Phase 1, and does not change as later phases land.
Each block carries a ``status``: Phase 1 returns real data for ``raw`` and
``condition`` and an honest ``not_implemented`` / ``insufficient_data`` for the
blocks whose engines arrive in Phases 2-5. A UI written against this today keeps
working as those blocks start returning numbers.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.enums import BlockStatus, Confidence, Decision, LiquidityBand, TrendDirection
from app.schemas.common import ApiModel

ENGINE_VERSION = "0.1.0"


class EvaluationBlock(ApiModel):
    status: str = BlockStatus.OK.value
    reason: str | None = Field(
        default=None, description="Why the block is not OK, in words a user can act on."
    )
    phase: int | None = Field(
        default=None, description="Which build phase populates this block, when it is not yet live."
    )


class ExplanationItem(ApiModel):
    """One line of the "Why?" panel (spec section 30)."""

    kind: str = Field(description="pass | warn | fail | info")
    text: str
    detail: str | None = None


class RawBlock(EvaluationBlock):
    card_id: str
    display_name: str
    set_label: str | None = None
    number: str | None = None
    variant: str | None = None
    language: str | None = None
    quantity: int = 1
    currency: str = "GBP"
    purchase_price: float | None = None
    user_raw_value: float | None = None
    market_raw_value: float | None = None
    best_raw_value: float | None = Field(
        default=None, description="User override if set, otherwise the market value."
    )
    raw_value_source: str | None = None
    net_raw_sale_value: float | None = Field(
        default=None, description="Raw value after selling fees, shipping and packaging."
    )


class ConditionScoreOut(ApiModel):
    centering: float | None = None
    centering_front: float | None = None
    centering_back: float | None = None
    corners: float | None = None
    edges: float | None = None
    surface: float | None = None
    overall: float | None = None


class ConditionBlock(EvaluationBlock):
    assessment_id: str | None = None
    assessed_at: datetime | None = None
    assessor: str | None = None
    completeness: float | None = None
    scores: ConditionScoreOut = Field(default_factory=ConditionScoreOut)
    notable_defects: list[str] = Field(default_factory=list)


class GradeProbability(ApiModel):
    grade: float
    label: str
    probability: float


class CompanyGradePrediction(ApiModel):
    """One grader's distribution. Companies differ, so they are not merged."""

    company_id: str | None = None
    company_code: str
    company_name: str | None = None
    probabilities: list[GradeProbability] = Field(default_factory=list)
    likely_grade: float | None = None
    grade_min: float | None = None
    grade_max: float | None = None
    max_grade_cap: float | None = None
    confidence: str = Confidence.NONE.value
    caps_applied: list[str] = Field(default_factory=list)
    is_user_override: bool = False


class GradePredictionBlock(EvaluationBlock):
    """The headline fields describe the first company in scope; ``by_company``
    carries every grader, and ``physical`` the company-agnostic view of the card
    itself (spec section 8 keeps those two questions apart)."""

    company_code: str | None = None
    kind: str | None = None
    source: str | None = None
    probabilities: list[GradeProbability] = Field(default_factory=list)
    likely_grade: float | None = None
    grade_min: float | None = None
    grade_max: float | None = None
    max_grade_cap: float | None = None
    confidence: str = Confidence.NONE.value
    caps_applied: list[str] = Field(default_factory=list)

    physical: CompanyGradePrediction | None = None
    by_company: list[CompanyGradePrediction] = Field(default_factory=list)
    model_version: str | None = None
    base_grade: float | None = Field(
        default=None, description="Weighted mean of the condition sub-scores, before any rules."
    )


class MarketValueRow(ApiModel):
    grade_label: str
    company_code: str | None = None
    grade: float | None = None
    median: float | None = None
    weighted_median: float | None = None
    low_quartile: float | None = None
    high_quartile: float | None = None
    last_sale: float | None = None
    realistic_sale: float | None = None
    quick_sale: float | None = None
    sample_size: int = 0
    window_days: int | None = None
    last_sale_at: date | None = None
    confidence: str = Confidence.NONE.value
    premium_vs_raw_pct: float | None = None
    is_user_override: bool = False


class MarketBlock(EvaluationBlock):
    currency: str = "GBP"
    raw: MarketValueRow | None = None
    graded: list[MarketValueRow] = Field(default_factory=list)
    computed_at: datetime | None = None
    sources: list[str] = Field(default_factory=list)


class LiquidityBlock(EvaluationBlock):
    score: float | None = None
    band: str = LiquidityBand.UNKNOWN.value
    sales_7d: int | None = None
    sales_30d: int | None = None
    sales_90d: int | None = None
    sales_365d: int | None = None
    days_since_last_sale: int | None = None
    active_listings: int | None = None
    sold_to_active_ratio: float | None = None
    median_days_between_sales: float | None = None
    sales_per_month: float | None = None


class TrendBlock(EvaluationBlock):
    direction: str = TrendDirection.INSUFFICIENT_DATA.value
    confidence: str = Confidence.NONE.value
    grade_label: str | None = Field(
        default=None,
        description=(
            "Which grade the direction describes. A trend across pooled grades measures which "
            "grades happened to sell, not whether prices moved."
        ),
    )
    change_7d_pct: float | None = None
    change_30d_pct: float | None = None
    change_90d_pct: float | None = None
    change_180d_pct: float | None = None
    change_365d_pct: float | None = None
    sample_size: int = 0


class GradingOption(ApiModel):
    """One (company, tier) route this card could take."""

    company_id: str
    company_code: str
    company_name: str
    tier_id: str | None = None
    tier_name: str | None = None
    currency: str = "GBP"
    declared_value: float | None = None
    grading_fee: float | None = None
    allocated_overhead: float | None = None
    total_cost: float | None = None
    turnaround_days: int | None = None
    minimum_cards: int = 1
    requires_batch: bool = False
    membership_required: bool = False
    available: bool = True
    blockers: list[str] = Field(default_factory=list)


class GradingOptionsBlock(EvaluationBlock):
    options: list[GradingOption] = Field(default_factory=list)


class OutcomeRow(ApiModel):
    grade: float
    label: str
    probability: float
    gross_value: float | None = None
    net_value: float | None = None
    profit: float | None = None


class ExpectedOutcome(ApiModel):
    company_code: str
    tier_name: str | None = None
    expected_gross: float | None = None
    expected_net: float | None = None
    expected_profit: float | None = None
    roi_pct: float | None = None
    probability_of_profit: float | None = None
    probability_of_target_profit: dict[str, float] = Field(default_factory=dict)
    minimum_profitable_grade: float | None = None
    downside: float | None = None
    upside: float | None = None
    liquidity_score: float | None = None
    opportunity_score: float | None = None
    rows: list[OutcomeRow] = Field(default_factory=list)


class ExpectedOutcomesBlock(EvaluationBlock):
    outcomes: list[ExpectedOutcome] = Field(default_factory=list)


class RecommendationBlock(EvaluationBlock):
    decision: str = Decision.INSUFFICIENT_DATA.value
    confidence: str = Confidence.NONE.value
    headline: str = ""
    company_code: str | None = None
    tier_name: str | None = None
    expected_profit: float | None = None
    roi_pct: float | None = None
    probability_of_profit: float | None = None
    minimum_profitable_grade: float | None = None
    opportunity_score: float | None = None
    alternative: ExpectedOutcome | None = Field(
        default=None,
        description="A route with better headline economics that was not chosen, and why (§26).",
    )
    alternative_note: str | None = None
    is_user_override: bool = False
    reasons: list[ExplanationItem] = Field(default_factory=list)


class CardEvaluation(ApiModel):
    card_id: str
    evaluated_at: datetime
    engine_version: str = ENGINE_VERSION
    currency: str = "GBP"

    raw: RawBlock
    condition: ConditionBlock
    grade_prediction: GradePredictionBlock
    market: MarketBlock
    liquidity: LiquidityBlock
    trend: TrendBlock
    grading_options: GradingOptionsBlock
    expected_outcomes: ExpectedOutcomesBlock
    recommendation: RecommendationBlock

    explanation: list[ExplanationItem] = Field(default_factory=list)
    blockers: list[str] = Field(
        default_factory=list,
        description="What is still missing before a real recommendation is possible.",
    )
    data_confidence: str = Confidence.NONE.value
