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

from app.enums import (
    BlockStatus,
    Confidence,
    Decision,
    LiquidityBand,
    PredictionSource,
    TrendDirection,
)
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

    # --- What your own returned grades changed (Phase 8) --------------------
    # The raw model output is kept beside the corrected one rather than
    # replaced. A silent adjustment is untrustworthy: you cannot tell whether a
    # prediction moved because the card is different or because the model
    # learned something.
    source: str = PredictionSource.RULES_ENGINE.value
    uncalibrated_likely_grade: float | None = Field(
        default=None, description="What the model said before your history was applied."
    )
    uncalibrated_probabilities: list[GradeProbability] = Field(default_factory=list)
    calibration_offset: float | None = Field(
        default=None,
        description="Grades the centre moved, learned from your results. Null when nothing "
        "was applied.",
    )
    calibration_sample_size: int | None = Field(
        default=None, description="How many of your returned grades that correction rests on."
    )
    calibration_note: str | None = None


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

    # Where the number came from. Null means your own sales, which is the
    # strongest evidence available and the reason it wins over a provider's
    # index. A code here means nobody has sold one that you have recorded, so
    # this is a third party's aggregate standing in.
    source_code: str | None = None
    source_name: str | None = None


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
    #: `sales` (your own records, which carry dates) or `reported_volume` (a
    #: source's yearly count, which cannot speak to recency).
    basis: str | None = None
    annual_volume: int | None = None


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


class NetValueRow(ApiModel):
    """What one grade actually nets after the platform has taken its cut."""

    grade_label: str
    grade: float | None = None
    gross: float | None = None
    shipping_income: float | None = None
    platform_fee: float | None = None
    payment_fee: float | None = None
    listing_fee: float | None = None
    postage_cost: float | None = None
    packaging_cost: float | None = None
    total_costs: float | None = None
    net: float | None = None
    is_graded: bool = False


class GradingOption(ApiModel):
    """One (company, tier) route this card could take."""

    company_id: str
    company_code: str
    company_name: str
    tier_id: str | None = None
    tier_name: str | None = None
    currency: str = "GBP"
    declared_value: float | None = None
    # The cost, broken out — a total with no working shown is a number to
    # argue with rather than act on.
    base_fee: float | None = None
    membership_discount: float | None = None
    grading_fee: float | None = None
    per_card_fees: float | None = None
    declared_value_fee: float | None = None
    allocated_overhead: float | None = None
    total_cost: float | None = None
    shared_total: float | None = Field(
        default=None, description="The submission-level pot before it was split."
    )
    assumed_batch_size: int = 1
    membership_code: str | None = None
    turnaround_days: int | None = None
    minimum_cards: int = 1
    requires_batch: bool = False
    membership_required: bool = False
    available: bool = True
    blockers: list[str] = Field(default_factory=list)


class CompanyBestCase(ApiModel):
    """The best a company could do for this card, priced in that company's own slabs.

    Kept per company on purpose: an ACE 10 does not sell for what a PSA 10
    sells for, so pairing the cheapest grading fee anywhere with the highest
    slab price anywhere would invent a route that does not exist.
    """

    company_id: str
    company_code: str
    tier_name: str | None = None
    grading_cost: float | None = None
    best_grade_label: str | None = None
    best_grade: float | None = None
    best_net: float | None = None
    upside_vs_raw: float | None = None
    reason: str | None = Field(
        default=None, description="Why this company has no best case, when it has none."
    )


class GradingOptionsBlock(EvaluationBlock):
    options: list[GradingOption] = Field(default_factory=list)
    currency: str = "GBP"
    best_case: list[CompanyBestCase] = Field(default_factory=list)

    declared_value: float | None = None
    declared_value_source: str = "system"
    declared_value_confidence: str = Confidence.NONE.value
    declared_value_basis: str | None = Field(
        default=None, description="What the suggested declared value was worked out from."
    )
    declared_value_coverage: float | None = Field(
        default=None,
        description="Share of the grade distribution covered by grades with sales data.",
    )

    assumed_batch_size: int = Field(
        default=1, description="How many cards the shared costs were split across."
    )
    allocation_method: str = "equal"
    allocation_note: str | None = None

    selling_profile_code: str | None = None
    selling_profile_name: str | None = None
    #: Net proceeds per grade. Tier-independent, so computed once rather than
    #: repeated inside every option.
    net_values: list[NetValueRow] = Field(default_factory=list)
    cheapest_available_cost: float | None = None


class OutcomeRow(ApiModel):
    grade: float
    label: str
    probability: float
    gross_value: float | None = None
    net_value: float | None = None
    profit: float | None = None


class ExpectedOutcome(ApiModel):
    """What one grading route is expected to produce, and how sure that is."""

    company_code: str
    tier_name: str | None = None
    grading_cost: float | None = None
    expected_gross: float | None = None
    expected_net: float | None = None
    expected_profit: float | None = Field(
        default=None,
        description="Probability-weighted profit *over selling the card raw today*.",
    )
    roi_pct: float | None = Field(
        default=None, description="Expected profit as a percentage of the grading fee."
    )
    probability_of_profit: float | None = None
    probability_of_target_profit: dict[str, float] = Field(default_factory=dict)
    minimum_profitable_grade: float | None = None
    probability_at_or_above_minimum: float | None = None
    downside: float | None = Field(
        default=None,
        description="Profit at a low percentile of the outcomes — not the worst grade on the ladder.",
    )
    upside: float | None = Field(default=None, description="Profit at the 90th percentile.")
    liquidity_score: float | None = Field(
        default=None, description="How readily this grader's slab of this card would sell."
    )
    opportunity_score: float | None = None
    score_parts: dict[str, float] = Field(default_factory=dict)
    coverage: float = Field(
        default=0.0,
        description="Share of the grade distribution with sales behind it. The rest is unknown.",
    )
    confidence: str = Confidence.NONE.value
    notes: list[str] = Field(default_factory=list)
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
    score_parts: dict[str, float] = Field(default_factory=dict)
    expected_net: float | None = None
    net_raw_alternative: float | None = Field(
        default=None, description="What selling it raw would net — the bar grading has to clear."
    )
    downside: float | None = None
    upside: float | None = None
    probability_of_target_profit: dict[str, float] = Field(default_factory=dict)
    grading_cost: float | None = None
    assumed_batch_size: int = 1
    coverage: float = Field(
        default=0.0,
        description=(
            "Share of the likely grades with sales behind them. Below 1.0 the expected "
            "figures are conditional on landing on a priced grade, and must be read as such."
        ),
    )
    review_in_days: int | None = Field(
        default=None, description="Set on a Hold: when to look at this card again (§33)."
    )
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
