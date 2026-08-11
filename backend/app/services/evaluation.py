"""``evaluate_card`` — the one function the UI is built around (spec section 45).

Phase 1 populates the blocks it can honestly populate (identity, ownership,
condition) and returns a specific, actionable status for the rest. It never
fabricates a number to fill a gap: a card with no sales data reports
``insufficient_data`` and lists what it needs, because a confident wrong answer
is worse than no answer (spec section 36).

Phases 2-5 replace the stub branches below with real engines. Nothing about the
response shape changes when they do.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import (
    DEFECT_FIELDS,
    BlockStatus,
    Confidence,
    Decision,
    DeclaredValueSource,
    PredictionKind,
    PredictionSource,
    Severity,
    TrendDirection,
)
from app.models import (
    Card,
    DataSource,
    GradingCompany,
    MarketPrice,
    MarketSale,
    SellingCostProfile,
)
from app.money import format_money, to_major, to_minor
from app.schemas.evaluation import (
    ENGINE_VERSION,
    CardEvaluation,
    CompanyBestCase,
    CompanyGradePrediction,
    ConditionBlock,
    ConditionScoreOut,
    ExpectedOutcome,
    ExpectedOutcomesBlock,
    ExplanationItem,
    GradePredictionBlock,
    GradeProbability,
    GradingOption,
    GradingOptionsBlock,
    LiquidityBlock,
    MarketBlock,
    MarketValueRow,
    NetValueRow,
    OutcomeRow,
    RawBlock,
    RecommendationBlock,
    TrendBlock,
)
from app.services import (
    cards_service,
    condition_service,
    decision,
    economics,
    market_service,
    prediction_service,
    predictions,
    settings_service,
)
from app.services.identity import grade_label as build_grade_label
from app.services.prediction_service import ModelParameters, NotEnoughAssessmentError

PHASE_GRADE_PREDICTION = 2
PHASE_MARKET = 3
PHASE_ECONOMICS = 4
PHASE_DECISION = 5

_NOT_ASSESSED_REASON = "No condition assessment recorded yet."
_NO_MARKET_REASON = (
    "No market data for this card yet. Add sales manually, import a CSV, or connect a "
    "data source."
)

# Weakest to strongest, so ``min``/``max`` over this list mean what they say.
_CONFIDENCE_ORDER = [
    Confidence.NONE.value,
    Confidence.LOW.value,
    Confidence.MEDIUM.value,
    Confidence.HIGH.value,
]
_GOOD_CONFIDENCE = {Confidence.MEDIUM.value, Confidence.HIGH.value}


def _confidence_phrase(confidence: str) -> str:
    """"none confidence" is not English. Say what it means instead."""
    return "no confidence" if confidence == Confidence.NONE.value else f"{confidence} confidence"


_TREND_FIELDS = {
    7: "change_7d_pct",
    30: "change_30d_pct",
    90: "change_90d_pct",
    180: "change_180d_pct",
    365: "change_365d_pct",
}

# Defects worth surfacing on the card page without a full assessment read.
_NOTABLE_SEVERITIES = {Severity.MODERATE.value, Severity.SEVERE.value}


def _display_name(card: Card) -> str:
    bits = [card.name]
    if card.card_number:
        bits.append(card.card_number)
    return " ".join(bits)


def _set_label(card: Card) -> str | None:
    if card.set_name and card.set_code:
        return f"{card.set_name} ({card.set_code})"
    return card.set_name or card.set_code


def _build_raw_block(
    card: Card,
    currency: str,
    market_raw: MarketPrice | None,
    profile: SellingCostProfile | None = None,
) -> RawBlock:
    market_value = None
    if market_raw is not None:
        # The user's own number wins, then the realistic-sale estimate, then the
        # bare median — in that order of how close it is to what they would get.
        market_value = to_major(
            market_raw.user_value_minor
            or market_raw.realistic_sale_minor
            or market_raw.median_minor
        )

    user_value = to_major(card.user_raw_value_minor)
    best = user_value if user_value is not None else market_value
    source = "user_override" if user_value is not None else ("market" if market_value is not None else None)

    # Selling it raw is the alternative every grading decision is measured
    # against, so it has to be netted the same way a graded sale is.
    net_raw = economics.net_sale_value(to_minor(best), profile, graded=False)

    return RawBlock(
        status=BlockStatus.OK.value,
        card_id=card.id,
        display_name=_display_name(card),
        set_label=_set_label(card),
        number=card.card_number,
        variant=card.variant,
        language=card.language,
        quantity=card.quantity,
        currency=currency,
        purchase_price=to_major(card.purchase_price_minor),
        user_raw_value=user_value,
        market_raw_value=market_value,
        best_raw_value=best,
        raw_value_source=source,
        net_raw_sale_value=to_major(net_raw.net_minor) if net_raw else None,
    )


def _build_condition_block(db: Session, card: Card) -> ConditionBlock:
    assessment = cards_service.current_condition(db, card.id)
    if assessment is None:
        return ConditionBlock(status=BlockStatus.NOT_ASSESSED.value, reason=_NOT_ASSESSED_REASON)

    notable: list[str] = []
    for face in ("front", "back"):
        for defect in DEFECT_FIELDS:
            severity = getattr(assessment, f"{face}_{defect}", None)
            if severity in _NOTABLE_SEVERITIES:
                label = defect.replace("_", " ")
                notable.append(f"{face.capitalize()} {label}: {severity}")

    completeness = assessment.completeness or 0.0
    status = BlockStatus.OK.value if completeness >= 0.5 else BlockStatus.PARTIAL.value
    reason = None if status == BlockStatus.OK.value else (
        f"Only {completeness:.0%} of the assessment is filled in — the grade estimate "
        "will be wide until the rest is answered."
    )

    return ConditionBlock(
        status=status,
        reason=reason,
        assessment_id=assessment.id,
        assessed_at=assessment.assessed_at,
        assessor=assessment.assessor,
        completeness=assessment.completeness,
        scores=ConditionScoreOut(
            centering=assessment.centering_score,
            centering_front=assessment.centering_score_front,
            centering_back=assessment.centering_score_back,
            corners=assessment.corners_score,
            edges=assessment.edges_score,
            surface=assessment.surface_score,
            overall=condition_service.overall_condition_score(assessment),
        ),
        notable_defects=notable,
    )


def _to_probabilities(raw: dict[str, float], company_code: str | None) -> list[GradeProbability]:
    items = [
        GradeProbability(
            grade=float(grade),
            label=f"{company_code} {grade}" if company_code else f"Grade {grade}",
            probability=float(probability),
        )
        for grade, probability in raw.items()
    ]
    return sorted(items, key=lambda item: item.grade, reverse=True)


def _build_grade_prediction_block(
    db: Session, card: Card, settings_values: dict
) -> GradePredictionBlock:
    """Grade probabilities, recomputed from the current assessment.

    Computed rather than read back from storage, so the numbers can never lag
    behind a reassessment. ``POST /grade-prediction`` is what persists a run,
    for history and for Phase 8 to score later. A prediction the user has
    overridden wins over the model's (spec section 35).
    """
    assessment = cards_service.current_condition(db, card.id)
    if assessment is None:
        return GradePredictionBlock(
            status=BlockStatus.NOT_ASSESSED.value,
            reason=_NOT_ASSESSED_REASON,
        )

    params = ModelParameters.from_settings(settings_values)
    try:
        physical = prediction_service.predict(
            assessment,
            company=None,
            rules=prediction_service.load_rules(db, None),
            params=params,
            kind=PredictionKind.PHYSICAL.value,
        )
    except NotEnoughAssessmentError as exc:
        return GradePredictionBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            reason=str(exc),
        )

    overrides = {
        row.company_id: row
        for row in predictions.current_predictions(db, card.id)
        if row.source == PredictionSource.USER_OVERRIDE.value
    }

    by_company: list[CompanyGradePrediction] = []
    for company in predictions.companies_for_prediction(db, settings_values):
        override = overrides.get(company.id)
        if override is not None:
            by_company.append(
                CompanyGradePrediction(
                    company_id=company.id,
                    company_code=company.code,
                    company_name=company.name,
                    probabilities=_to_probabilities(override.probabilities or {}, company.code),
                    likely_grade=override.likely_grade,
                    grade_min=override.grade_min,
                    grade_max=override.grade_max,
                    max_grade_cap=override.max_grade_cap,
                    confidence=override.confidence,
                    caps_applied=[],
                    is_user_override=True,
                )
            )
            continue

        result = prediction_service.predict(
            assessment,
            company=company,
            rules=prediction_service.load_rules(db, company.id),
            params=params,
            kind=PredictionKind.MARKET.value,
        )
        by_company.append(
            CompanyGradePrediction(
                company_id=company.id,
                company_code=company.code,
                company_name=company.name,
                probabilities=_to_probabilities(result.probabilities, company.code),
                likely_grade=result.likely_grade,
                grade_min=result.grade_min,
                grade_max=result.grade_max,
                max_grade_cap=result.max_grade_cap,
                confidence=result.confidence,
                caps_applied=[cap["label"] for cap in result.caps_applied],
                is_user_override=False,
            )
        )

    if not by_company:
        return GradePredictionBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            reason="No active grading company is configured to predict against.",
            physical=CompanyGradePrediction(
                company_code="physical",
                probabilities=_to_probabilities(physical.probabilities, None),
                likely_grade=physical.likely_grade,
                grade_min=physical.grade_min,
                grade_max=physical.grade_max,
                max_grade_cap=physical.max_grade_cap,
                confidence=physical.confidence,
            ),
        )

    headline = by_company[0]
    completeness = float(assessment.completeness or 0.0)
    status = BlockStatus.OK.value if completeness >= 0.6 else BlockStatus.PARTIAL.value

    return GradePredictionBlock(
        status=status,
        reason=(
            None
            if status == BlockStatus.OK.value
            else (
                f"Only {completeness:.0%} of the assessment is answered, so the range is wide. "
                "Finish it to narrow the estimate."
            )
        ),
        company_code=headline.company_code,
        kind=PredictionKind.MARKET.value,
        source=(
            PredictionSource.USER_OVERRIDE.value
            if headline.is_user_override
            else PredictionSource.RULES_ENGINE.value
        ),
        probabilities=headline.probabilities,
        likely_grade=headline.likely_grade,
        grade_min=headline.grade_min,
        grade_max=headline.grade_max,
        max_grade_cap=headline.max_grade_cap,
        confidence=headline.confidence,
        caps_applied=headline.caps_applied,
        physical=CompanyGradePrediction(
            company_code="physical",
            probabilities=_to_probabilities(physical.probabilities, None),
            likely_grade=physical.likely_grade,
            grade_min=physical.grade_min,
            grade_max=physical.grade_max,
            max_grade_cap=physical.max_grade_cap,
            confidence=physical.confidence,
        ),
        by_company=by_company,
        model_version=physical.model_version,
        base_grade=physical.base_grade,
    )


def _headline_probabilities(block: GradePredictionBlock) -> tuple[dict[float, float] | None, str | None]:
    """The distribution to value a declared value against, and whose ladder it is."""
    if not block.by_company:
        return None, None
    company = block.by_company[0]
    if not company.probabilities:
        return None, None
    return (
        {item.grade: item.probability for item in company.probabilities},
        company.company_code,
    )


def _best_case_per_company(
    options: list[GradingOption],
    net_rows: list[NetValueRow],
    raw_net: NetValueRow | None,
) -> list[CompanyBestCase]:
    """The best outcome each company could produce, priced in its own slabs.

    Strictly within a company: the cheapest tier *that company* offers, against
    the best-netting grade *that company* has sales data for. Pairing ACE's fee
    with PSA's slab price would describe a route that does not exist.
    """
    results: list[CompanyBestCase] = []
    by_company: dict[str, list[GradingOption]] = {}
    for option in options:
        by_company.setdefault(option.company_code, []).append(option)

    for code, group in by_company.items():
        usable = [item for item in group if item.available and item.total_cost is not None]
        row = CompanyBestCase(company_id=group[0].company_id, company_code=code)

        if not usable:
            row.reason = f"No usable {code} tier for this card."
            results.append(row)
            continue

        cheapest = min(usable, key=lambda item: item.total_cost or float("inf"))
        row.tier_name = cheapest.tier_name
        row.grading_cost = cheapest.total_cost

        owned = [
            item
            for item in net_rows
            if item.is_graded and item.grade_label.split(" ")[0].upper() == code.upper()
        ]
        if not owned:
            row.reason = f"No {code} sales stored, so {code} slabs cannot be priced."
            results.append(row)
            continue

        best = max(owned, key=lambda item: item.net or float("-inf"))
        row.best_grade_label = best.grade_label
        row.best_grade = best.grade
        row.best_net = best.net
        if best.net is not None and raw_net is not None and raw_net.net is not None:
            row.upside_vs_raw = round(best.net - raw_net.net - (cheapest.total_cost or 0), 2)
        results.append(row)

    results.sort(key=lambda item: item.upside_vs_raw if item.upside_vs_raw is not None else -1e9,
                 reverse=True)
    return results


def _build_grading_options_block(
    db: Session,
    card: Card,
    settings_values: dict,
    *,
    summary: market_service.MarketSummary,
    grade_block: GradePredictionBlock,
    currency: str,
    batch_size: int | None = None,
) -> GradingOptionsBlock:
    """What each grading route would actually cost this card.

    Costing a single card means assuming a submission around it, because
    shipping and insurance belong to the parcel rather than the card. The
    assumed batch size travels with every figure, so "£20.60 per card" is never
    read without "if you send twenty-five".
    """
    wanted = settings_values.get("default_grading_company_codes") or []
    companies = list(
        db.scalars(
            select(GradingCompany)
            .where(GradingCompany.active.is_(True))
            .order_by(GradingCompany.sort_order)
        )
    )
    if wanted:
        companies = [company for company in companies if company.code in wanted] or companies

    probabilities, company_code = _headline_probabilities(grade_block)
    declared = economics.suggest_declared_value(
        card, prices=summary.prices, probabilities=probabilities, company_code=company_code
    )
    if card.user_declared_value_minor is not None:
        declared = economics.DeclaredValue(
            value_minor=card.user_declared_value_minor,
            source=DeclaredValueSource.USER.value,
            confidence=Confidence.HIGH.value,
            basis="Your own figure. The engine's suggestion is kept alongside it, not replaced.",
        )

    assumptions = economics.SubmissionAssumptions.from_settings(
        settings_values, batch_size=batch_size or 1
    )
    profile = economics.default_profile(db)

    options: list[GradingOption] = []
    for company in companies:
        tiers = economics.eligible_tiers(
            company,
            declared_value_minor=declared.value_minor,
            batch_size=assumptions.batch_size,
            today=date.today(),
        )
        if not tiers:
            options.append(
                GradingOption(
                    company_id=company.id,
                    company_code=company.code,
                    company_name=company.name,
                    currency=company.currency,
                    declared_value=to_major(declared.value_minor),
                    available=False,
                    blockers=[
                        f"No active tier configured for {company.code}. "
                        "Add current pricing in Settings → Grading."
                    ],
                )
            )
            continue

        for tier, blockers in tiers:
            costing = economics.cost_for_tier(
                tier,
                company,
                declared_value_minor=declared.value_minor,
                assumptions=assumptions,
                blockers=blockers,
            )
            cost = costing.cost
            priced = tier.price_minor > 0
            options.append(
                GradingOption(
                    company_id=company.id,
                    company_code=company.code,
                    company_name=company.name,
                    tier_id=tier.id,
                    tier_name=tier.tier_name,
                    currency=tier.currency,
                    declared_value=to_major(declared.value_minor),
                    base_fee=to_major(cost.base_fee_minor) if priced else None,
                    membership_discount=to_major(cost.membership_discount_minor) or None,
                    grading_fee=to_major(cost.grading_fee_minor) if priced else None,
                    per_card_fees=to_major(cost.per_card_fees_minor) or None,
                    declared_value_fee=to_major(cost.declared_value_fee_minor) or None,
                    allocated_overhead=to_major(cost.allocated_overhead_minor),
                    # An unpriced tier gets no total: costing it at the shared
                    # overhead alone would read as a cheap route.
                    total_cost=to_major(cost.total_minor) if priced else None,
                    shared_total=to_major(cost.shared_total_minor) or None,
                    assumed_batch_size=assumptions.batch_size,
                    membership_code=costing.membership_code,
                    turnaround_days=tier.turnaround_days,
                    minimum_cards=tier.minimum_cards,
                    requires_batch=tier.minimum_cards > 1,
                    membership_required=tier.membership_required,
                    available=costing.available,
                    blockers=costing.blockers,
                )
            )

    nets = economics.net_by_grade(summary, profile)
    net_rows = [
        NetValueRow(
            grade_label=label,
            grade=next(
                (row.grade for row in summary.prices if row.grade_label == label), None
            ),
            gross=to_major(value.gross_minor),
            shipping_income=to_major(value.shipping_income_minor) or None,
            platform_fee=to_major(value.platform_fee_minor) or None,
            payment_fee=to_major(value.payment_fee_minor) or None,
            listing_fee=to_major(value.listing_fee_minor) or None,
            postage_cost=to_major(value.postage_cost_minor) or None,
            packaging_cost=to_major(value.packaging_cost_minor) or None,
            total_costs=to_major(value.total_costs_minor),
            net=to_major(value.net_minor),
            is_graded=value.is_graded,
        )
        for label, value in sorted(nets.items(), key=lambda item: item[0] != "raw")
    ]

    available = [option for option in options if option.available and option.total_cost is not None]
    cheapest = min((option.total_cost for option in available), default=None)
    raw_net_row = next((row for row in net_rows if not row.is_graded), None)
    best_case = _best_case_per_company(options, net_rows, raw_net_row)

    reasons: list[str] = []
    if not options:
        status = BlockStatus.INSUFFICIENT_DATA.value
        reasons.append("No active grading company is configured.")
    elif declared.value_minor is None:
        status = BlockStatus.PARTIAL.value
        reasons.append(
            "Costs shown without a declared value, so tier ceilings and any percentage-of-value "
            "fees are not applied. Add comparable sales or your own estimate."
        )
    elif profile is None:
        status = BlockStatus.PARTIAL.value
        reasons.append(
            "No selling profile is configured, so net proceeds cannot be worked out. "
            "Add one in Settings → Selling."
        )
    elif not available:
        status = BlockStatus.PARTIAL.value
        reasons.append("No tier is usable for this card as things stand — see the reasons below.")
    else:
        status = BlockStatus.OK.value

    if assumptions.allocation_note:
        reasons.append(assumptions.allocation_note)
    if declared.confidence in {Confidence.NONE.value, Confidence.LOW.value} and options:
        reasons.append(
            f"Declared value is a {_confidence_phrase(declared.confidence)} estimate, and it "
            "drives tier eligibility — check it before submitting."
        )

    return GradingOptionsBlock(
        status=status,
        phase=None if status == BlockStatus.OK.value else PHASE_ECONOMICS,
        reason=" ".join(reasons) or None,
        currency=currency,
        options=options,
        declared_value=to_major(declared.value_minor),
        declared_value_source=declared.source,
        declared_value_confidence=declared.confidence,
        declared_value_basis=declared.basis,
        declared_value_coverage=declared.coverage,
        assumed_batch_size=assumptions.batch_size,
        allocation_method=assumptions.allocation_method,
        allocation_note=assumptions.allocation_note,
        selling_profile_code=profile.code if profile else None,
        selling_profile_name=profile.name if profile else None,
        net_values=net_rows,
        best_case=best_case,
        cheapest_available_cost=cheapest,
    )


def _value_row(
    row: MarketPrice, company_codes: dict[str, str], premium: float | None
) -> MarketValueRow:
    return MarketValueRow(
        grade_label=row.grade_label,
        company_code=company_codes.get(row.company_id or ""),
        grade=row.grade,
        median=to_major(row.median_minor),
        weighted_median=to_major(row.weighted_median_minor),
        low_quartile=to_major(row.low_quartile_minor),
        high_quartile=to_major(row.high_quartile_minor),
        last_sale=to_major(row.last_sale_minor),
        # The user's own figure is what they will act on, so it is what the
        # decision engine downstream should read (spec section 35).
        realistic_sale=to_major(row.user_value_minor or row.realistic_sale_minor),
        quick_sale=to_major(row.quick_sale_minor),
        sample_size=row.sample_size,
        window_days=row.window_days,
        last_sale_at=row.last_sale_at,
        confidence=row.confidence,
        premium_vs_raw_pct=premium,
        is_user_override=row.user_value_minor is not None,
    )


def _build_market_block(
    db: Session, summary: market_service.MarketSummary, currency: str
) -> MarketBlock:
    """Valuations per grade, or an honest account of why there are none."""
    if not summary.catalog_key:
        return MarketBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            phase=PHASE_MARKET,
            reason="This card has no catalog key, so sales cannot be matched to it.",
            currency=currency,
        )
    if not summary.prices:
        # ``sale_count`` counts only the usable ones, so "no sales at all" and
        # "sales that were all filtered out" are different situations and need
        # different advice.
        reason = _NO_MARKET_REASON
        if summary.excluded_count:
            reason = (
                f"{summary.excluded_count} sale(s) stored, all excluded as non-comparable "
                "(lots, damage, wrong language or variant). Review the exclusions if that "
                "looks wrong — every one is reversible."
            )
        return MarketBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            phase=PHASE_MARKET,
            reason=reason,
            currency=currency,
        )

    company_codes = {company.id: company.code for company in db.scalars(select(GradingCompany))}
    raw_row = summary.raw
    raw = _value_row(raw_row, company_codes, None) if raw_row is not None else None
    graded = [
        _value_row(row, company_codes, market_service.premium_vs_raw_pct(raw_row, row))
        for row in summary.graded
    ]

    best = max(
        (row.confidence for row in summary.prices),
        key=_CONFIDENCE_ORDER.index,
        default=Confidence.NONE.value,
    )
    status = (
        BlockStatus.OK.value
        if best in {Confidence.HIGH.value, Confidence.MEDIUM.value}
        else BlockStatus.PARTIAL.value
    )
    reason = None
    if status != BlockStatus.OK.value:
        thin = raw_row or summary.prices[0]
        reason = (
            f"Thin evidence: {thin.sample_size} sale(s) in the last {thin.window_days} days. "
            "Treat these figures as indicative."
        )
    if raw is None:
        note = "No raw sales stored, so there is nothing to compare a slab against."
        reason = f"{reason} {note}" if reason else note
        status = BlockStatus.PARTIAL.value

    return MarketBlock(
        status=status,
        reason=reason,
        currency=currency,
        raw=raw,
        graded=graded,
        computed_at=summary.computed_at,
        sources=sorted({source for source in (_source_labels(db, summary.catalog_key)) if source}),
    )


def _source_labels(db: Session, catalog_key: str) -> list[str]:
    rows = db.execute(
        select(DataSource.name)
        .join(MarketSale, MarketSale.source_id == DataSource.id)
        .where(MarketSale.catalog_key == catalog_key)
        .distinct()
    )
    return [name for (name,) in rows]


def _build_liquidity_block(summary: market_service.MarketSummary) -> LiquidityBlock:
    liquidity = summary.liquidity
    if liquidity.score is None:
        return LiquidityBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            phase=PHASE_MARKET,
            reason="Liquidity needs sales history. No comparable sales are stored for this card.",
        )
    # A liquidity score from three sales is a description of three sales.
    status = (
        BlockStatus.OK.value if liquidity.sales_365d >= 6 else BlockStatus.PARTIAL.value
    )
    return LiquidityBlock(
        status=status,
        reason=(
            None
            if status == BlockStatus.OK.value
            else f"Based on {liquidity.sales_365d} sale(s) in a year — a thin basis for a score."
        ),
        score=liquidity.score,
        band=liquidity.band,
        sales_7d=liquidity.sales_7d,
        sales_30d=liquidity.sales_30d,
        sales_90d=liquidity.sales_90d,
        sales_365d=liquidity.sales_365d,
        days_since_last_sale=liquidity.days_since_last_sale,
        active_listings=liquidity.active_listings,
        sold_to_active_ratio=liquidity.sold_to_active_ratio,
        median_days_between_sales=liquidity.median_days_between_sales,
        sales_per_month=liquidity.sales_per_month,
    )


def _build_trend_block(summary: market_service.MarketSummary) -> TrendBlock:
    trend = summary.trend
    if trend.direction == TrendDirection.INSUFFICIENT_DATA.value:
        return TrendBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            phase=PHASE_MARKET,
            reason=(
                "A trend needs sales in two comparable periods, not a single price. "
                f"{trend.sample_size} sale(s) stored."
            ),
            sample_size=trend.sample_size,
        )
    grade = trend.grade_label or "raw"
    block = TrendBlock(
        status=(
            BlockStatus.OK.value
            if trend.confidence in _GOOD_CONFIDENCE
            else BlockStatus.PARTIAL.value
        ),
        direction=trend.direction,
        confidence=trend.confidence,
        sample_size=trend.sample_size,
        grade_label=trend.grade_label,
        reason=(
            f"{'Raw' if grade == 'raw' else grade} prices only — a trend across pooled grades "
            "measures which grades happened to sell, not whether prices moved."
        ),
    )
    if block.status != BlockStatus.OK.value:
        block.reason = (
            f"Direction from {trend.sample_size} {grade} sale(s). A 25% move off three sales "
            "is not the same claim as a 12% move off a hundred and fifty."
        )
    for horizon, field_name in _TREND_FIELDS.items():
        setattr(block, field_name, trend.changes.get(horizon))
    return block


def _data_confidence(
    condition_block: ConditionBlock, grade_block: GradePredictionBlock, market_block: MarketBlock
) -> str:
    """How much the whole picture deserves to be trusted.

    The weakest link, not the average: a perfect condition assessment with two
    sales behind it is still a two-sale answer.
    """
    parts = [
        Confidence.HIGH.value
        if (condition_block.completeness or 0) >= 0.85
        else Confidence.MEDIUM.value
        if (condition_block.completeness or 0) >= 0.5
        else Confidence.LOW.value
        if condition_block.status != BlockStatus.NOT_ASSESSED.value
        else Confidence.NONE.value,
        grade_block.confidence,
        market_block.raw.confidence if market_block.raw is not None else Confidence.NONE.value,
    ]
    return min(parts, key=_CONFIDENCE_ORDER.index)


def evaluate_card(db: Session, card: Card, *, batch_size: int | None = None) -> CardEvaluation:
    """``batch_size`` is how many cards to assume share a submission's shipping.

    Defaults to one, which is the honest worst case: a single card carries the
    whole £40 of postage. The UI lets the user try other sizes, because the
    same card can be unprofitable alone and clearly worth grading in a batch.
    """
    settings_values = settings_service.get_all(db)
    currency = settings_values.get("currency", "GBP")

    summary = market_service.summarise(
        db,
        card.catalog_key,
        params=market_service.MarketParameters.from_settings(settings_values),
        currency=currency,
    )

    profile = economics.default_profile(db)
    raw_block = _build_raw_block(card, currency, summary.raw, profile)
    condition_block = _build_condition_block(db, card)
    grade_block = _build_grade_prediction_block(db, card, settings_values)
    options_block = _build_grading_options_block(
        db,
        card,
        settings_values,
        summary=summary,
        grade_block=grade_block,
        currency=currency,
        batch_size=batch_size,
    )

    market_block = _build_market_block(db, summary, currency)
    liquidity_block = _build_liquidity_block(summary)
    trend_block = _build_trend_block(summary)
    outcomes_block, decision_result = _build_decision(
        db,
        card,
        settings_values,
        summary=summary,
        grade_block=grade_block,
        options_block=options_block,
        raw_block=raw_block,
        market_block=market_block,
        liquidity_block=liquidity_block,
        trend_block=trend_block,
        currency=currency,
        batch_size=batch_size or 1,
    )

    explanation, blockers = _explain(
        card, condition_block, grade_block, options_block, market_block, summary
    )
    blockers.extend(decision_result.blockers if decision_result else [])
    # Blockers answer "what would change this?". For a card ruled out on price
    # alone, nothing in the market data would — telling someone to go and find
    # graded sales for a £3.50 common is busywork dressed as advice.
    if decision_result is not None and decision_result.decision == Decision.DO_NOT_GRADE.value:
        blockers = [
            item for item in blockers if not item.startswith("Add graded sales")
        ]
    recommendation = _recommend(
        card,
        blockers,
        explanation,
        result=decision_result,
        raw_block=raw_block,
        currency=currency,
        batch_size=batch_size or 1,
    )

    return CardEvaluation(
        card_id=card.id,
        evaluated_at=datetime.now(UTC),
        engine_version=ENGINE_VERSION,
        currency=currency,
        raw=raw_block,
        condition=condition_block,
        grade_prediction=grade_block,
        market=market_block,
        liquidity=liquidity_block,
        trend=trend_block,
        grading_options=options_block,
        expected_outcomes=outcomes_block,
        recommendation=recommendation,
        explanation=explanation,
        blockers=blockers,
        data_confidence=_data_confidence(condition_block, grade_block, market_block),
    )


def _explain(
    card: Card,
    condition_block: ConditionBlock,
    grade_block: GradePredictionBlock,
    options_block: GradingOptionsBlock,
    market_block: MarketBlock,
    summary: market_service.MarketSummary,
) -> tuple[list[ExplanationItem], list[str]]:
    """Build the "Why?" panel and the list of what is still missing (§30)."""
    items: list[ExplanationItem] = []
    blockers: list[str] = []

    front_images = [image for image in card.images if image.side == "front"]
    back_images = [image for image in card.images if image.side == "back"]
    if front_images and back_images:
        items.append(ExplanationItem(kind="pass", text="Front and back photographs on file."))
    else:
        missing = " and ".join(
            side for side, present in (("front", front_images), ("back", back_images)) if not present
        )
        items.append(
            ExplanationItem(
                kind="warn",
                text=f"No {missing} photograph.",
                detail="Photographs are what make a condition assessment checkable later.",
            )
        )

    if condition_block.status == BlockStatus.NOT_ASSESSED.value:
        items.append(ExplanationItem(kind="fail", text="Condition not assessed."))
        blockers.append("Assess the card's condition.")
    else:
        overall = condition_block.scores.overall
        detail = f"Overall condition score {overall:.1f}/10." if overall is not None else None
        items.append(
            ExplanationItem(
                kind="pass" if condition_block.status == BlockStatus.OK.value else "warn",
                text=f"Condition assessed ({(condition_block.completeness or 0):.0%} complete).",
                detail=detail,
            )
        )
        if condition_block.notable_defects:
            items.append(
                ExplanationItem(
                    kind="warn",
                    text=f"{len(condition_block.notable_defects)} notable defect(s) recorded.",
                    detail="; ".join(condition_block.notable_defects[:4]),
                )
            )

    if grade_block.status in {BlockStatus.OK.value, BlockStatus.PARTIAL.value}:
        top = grade_block.probabilities[0] if grade_block.probabilities else None
        span = f"{grade_block.grade_min:g}–{grade_block.grade_max:g}"
        detail = f"{top.label} at {top.probability:.0%}, range {span}." if top is not None else None
        items.append(
            ExplanationItem(
                kind="pass" if grade_block.status == BlockStatus.OK.value else "warn",
                text=f"Likely {grade_block.company_code} {grade_block.likely_grade:g} "
                f"({_confidence_phrase(grade_block.confidence)}).",
                detail=detail,
            )
        )
        if grade_block.caps_applied:
            items.append(
                ExplanationItem(
                    kind="warn",
                    text=f"Capped at {grade_block.max_grade_cap:g} by "
                    f"{len(grade_block.caps_applied)} rule(s).",
                    detail="; ".join(grade_block.caps_applied[:3]),
                )
            )
        if grade_block.status == BlockStatus.PARTIAL.value:
            blockers.append("Finish the condition assessment to narrow the grade estimate.")

    items.extend(_market_explanation(market_block, summary, blockers))

    items.extend(_economics_explanation(options_block, market_block, blockers))

    if card.user_raw_value_minor is None and card.purchase_price_minor is None:
        # Saying "no raw value" when the market has valued the card contradicts
        # the figure two lines above it. What is missing is *your* number, and
        # that only matters when nothing else fills the gap.
        valued = market_block.raw is not None
        items.append(
            ExplanationItem(
                kind="info" if valued else "warn",
                text=(
                    "Raw value is the market's, not yours."
                    if valued
                    else "No raw value recorded."
                ),
                detail=(
                    "Set your own estimate if you would not actually sell at the market median."
                    if valued
                    else "A purchase price or your own raw estimate gives grading something to beat."
                ),
            )
        )

    return items, blockers


def _market_explanation(
    market_block: MarketBlock,
    summary: market_service.MarketSummary,
    blockers: list[str],
) -> list[ExplanationItem]:
    """The market half of the "Why?" panel, and what it still needs."""
    items: list[ExplanationItem] = []

    if summary.sale_count == 0:
        if summary.excluded_count:
            items.append(
                ExplanationItem(
                    kind="fail",
                    text=f"All {summary.excluded_count} stored sale(s) were filtered out.",
                    detail="Open the sales list to see why, and include any that were wrong.",
                )
            )
            blockers.append(
                "Every stored sale was excluded as non-comparable. Review the exclusions or "
                "add sales of the card itself."
            )
        else:
            items.append(ExplanationItem(kind="fail", text="No comparable sales stored."))
            blockers.append("Add comparable sales for the raw card and each relevant grade.")
        return items

    detail = f"{summary.sale_count} counted"
    if summary.excluded_count:
        detail += f", {summary.excluded_count} excluded as non-comparable"
    items.append(
        ExplanationItem(
            kind="pass" if market_block.status == BlockStatus.OK.value else "warn",
            text=f"{summary.sale_count} comparable sale(s) stored locally.",
            detail=f"{detail}. Every exclusion is listed and reversible.",
        )
    )

    raw = market_block.raw
    if raw is not None and raw.realistic_sale is not None:
        items.append(
            ExplanationItem(
                kind="pass" if raw.confidence in _GOOD_CONFIDENCE else "warn",
                text=(
                    f"Raw value {format_money(to_minor(raw.realistic_sale), market_block.currency)}"
                    f" ({_confidence_phrase(raw.confidence)})."
                ),
                detail=(
                    f"{raw.sample_size} sale(s) in {raw.window_days} days"
                    + (f", last on {raw.last_sale_at:%d %b %Y}" if raw.last_sale_at else "")
                    + "."
                ),
            )
        )
    elif raw is None:
        items.append(ExplanationItem(kind="warn", text="No raw sales stored for this card."))
        blockers.append("Add raw sales — grading profit is measured against selling it raw.")

    if not market_block.graded:
        blockers.append(
            "Add graded sales for the grades this card could realistically get, so the "
            "upside can be measured rather than assumed."
        )
    else:
        best = max(
            market_block.graded,
            key=lambda row: row.premium_vs_raw_pct or float("-inf"),
        )
        if best.premium_vs_raw_pct is not None:
            items.append(
                ExplanationItem(
                    kind="info",
                    text=f"{best.grade_label} sells {best.premium_vs_raw_pct:+.0f}% against raw.",
                    detail=f"{best.sample_size} sale(s) behind that figure.",
                )
            )

    liquidity = summary.liquidity
    if liquidity.score is not None:
        band = liquidity.band.replace("_", " ")
        kind = "warn" if liquidity.score < 5 else "pass"
        gap = (
            f"Median {liquidity.median_days_between_sales:.0f} days between sales."
            if liquidity.median_days_between_sales
            else None
        )
        items.append(
            ExplanationItem(
                kind=kind,
                text=f"Liquidity {liquidity.score:.1f}/10 — {band}.",
                detail=gap,
            )
        )
        if liquidity.score < 3:
            blockers.append(
                "This card barely trades. Check you could actually sell the slab before "
                "spending on grading."
            )

    trend = summary.trend
    if trend.direction != TrendDirection.INSUFFICIENT_DATA.value:
        horizon = next(
            (days for days in (90, 180, 30, 365, 7) if trend.changes.get(days) is not None), None
        )
        change = trend.changes.get(horizon) if horizon else None
        grade = trend.grade_label or "raw"
        items.append(
            ExplanationItem(
                kind="info",
                text=f"Trend {trend.direction.replace('_', ' ')} ({_confidence_phrase(trend.confidence)}).",
                detail=(
                    f"{change:+.1f}% over {horizon} days on {grade} sales."
                    if change is not None
                    else None
                ),
            )
        )

    return items


def _economics_explanation(
    options: GradingOptionsBlock,
    market: MarketBlock,
    blockers: list[str],
) -> list[ExplanationItem]:
    """The money half of the "Why?" panel: what grading costs, and what a sale keeps."""
    items: list[ExplanationItem] = []
    currency = options.currency

    available = [option for option in options.options if option.available]
    if not available:
        items.append(ExplanationItem(kind="fail", text="No usable grading tier for this card."))
        blockers.append(
            "Enter current pricing for at least one grading company, or check the tier "
            "restrictions listed under Grading routes."
        )
    else:
        cheapest = min(available, key=lambda option: option.total_cost or float("inf"))
        batch = (
            f" in a batch of {cheapest.assumed_batch_size}"
            if cheapest.assumed_batch_size > 1
            else " sending it on its own"
        )
        items.append(
            ExplanationItem(
                kind="pass",
                text=(
                    f"Cheapest route {cheapest.company_code} {cheapest.tier_name} at "
                    f"{format_money(to_minor(cheapest.total_cost), currency)}{batch}."
                ),
                detail=(
                    f"{format_money(to_minor(cheapest.grading_fee), currency)} fee plus "
                    f"{format_money(to_minor(cheapest.allocated_overhead), currency)} share of "
                    "shipping and insurance."
                ),
            )
        )

    if options.declared_value is not None:
        source = "yours" if options.declared_value_source == "user" else "estimated"
        items.append(
            ExplanationItem(
                kind="pass" if options.declared_value_confidence in _GOOD_CONFIDENCE else "warn",
                text=(
                    f"Declared value {format_money(to_minor(options.declared_value), currency)} "
                    f"({source}, {_confidence_phrase(options.declared_value_confidence)})."
                ),
                detail=options.declared_value_basis,
            )
        )

    raw_net = next((row for row in options.net_values if not row.is_graded), None)
    if raw_net is not None and raw_net.net is not None:
        kept = raw_net.net / raw_net.gross if raw_net.gross else None
        items.append(
            ExplanationItem(
                kind="info",
                text=(
                    f"Selling it raw nets {format_money(to_minor(raw_net.net), currency)} "
                    f"after fees and postage."
                ),
                detail=(
                    f"You keep {kept:.0%} of the sale price on "
                    f"{options.selling_profile_name}."
                    if kept is not None
                    else None
                ),
            )
        )

    # Best case, strictly within one company: an ACE 10 does not sell for what
    # a PSA 10 sells for, so the fee and the slab price must come from the same
    # grader or the route described does not exist.
    priced = [row for row in options.best_case if row.upside_vs_raw is not None]
    if priced:
        best = priced[0]
        items.append(
            ExplanationItem(
                kind="info",
                text=(
                    f"Best case {best.best_grade_label} nets "
                    f"{format_money(to_minor(best.best_net), currency)} — "
                    f"{format_money(to_minor(best.upside_vs_raw), currency)} over selling raw, "
                    f"after {best.company_code} {best.tier_name} at "
                    f"{format_money(to_minor(best.grading_cost), currency)}."
                ),
                detail=(
                    "Best case only: it assumes the top grade you have "
                    f"{best.company_code} sales data for. The recommendation above weighs "
                    "that against every other grade it might get."
                ),
            )
        )
    elif options.best_case and market.status != BlockStatus.INSUFFICIENT_DATA.value:
        missing = [row.company_code for row in options.best_case if row.best_net is None]
        blockers.append(
            "Add graded sales for "
            + ", ".join(sorted(missing))
            + " so the upside can be netted rather than guessed at."
        )

    return items


def _build_decision(
    db: Session,
    card: Card,
    settings_values: dict,
    *,
    summary: market_service.MarketSummary,
    grade_block: GradePredictionBlock,
    options_block: GradingOptionsBlock,
    raw_block: RawBlock,
    market_block: MarketBlock,
    liquidity_block: LiquidityBlock,
    trend_block: TrendBlock,
    currency: str,
    batch_size: int,
) -> tuple[ExpectedOutcomesBlock, decision.DecisionResult | None]:
    """Expected value per route, and the verdict that falls out of it.

    Every input is already computed by an earlier block, so this is arithmetic
    over what the page is already showing rather than a second opinion. The
    decision is derived from the same numbers the user can see.
    """
    thresholds = decision.Thresholds.from_settings(settings_values)
    net_by_label = {
        row.grade_label: to_minor(row.net) or 0
        for row in options_block.net_values
        if row.net is not None
    }
    gross_by_label = {
        row.grade_label: to_minor(row.gross) or 0
        for row in options_block.net_values
        if row.gross is not None
    }
    raw_net_minor = to_minor(raw_block.net_raw_sale_value)
    raw_value_minor = to_minor(raw_block.best_raw_value)

    sales_by_label: dict[str, int] = {}
    if card.catalog_key:
        for sale in market_service.usable_sales(db, card.catalog_key):
            sales_by_label[sale.grade_label] = sales_by_label.get(sale.grade_label, 0) + 1

    inputs = decision.DecisionInputs(
        raw_net_minor=raw_net_minor,
        raw_value_minor=raw_value_minor,
        liquidity_score=liquidity_block.score,
        trend_direction=trend_block.direction,
        trend_confidence=trend_block.confidence,
        market_confidence=(market_block.raw.confidence if market_block.raw else Confidence.NONE.value),
        grade_confidence=grade_block.confidence,
        sales_by_label=sales_by_label,
        market_recognition={
            company.code: company.market_recognition_score
            for company in db.scalars(select(GradingCompany))
        },
    )

    probabilities_by_company = {
        item.company_code: {row.grade: row.probability for row in item.probabilities}
        for item in grade_block.by_company
    }

    if not probabilities_by_company or raw_net_minor is None:
        reason = (
            "Expected value needs grade probabilities — assess the card first."
            if not probabilities_by_company
            else "Expected value needs a raw value to measure grading against."
        )
        return (
            ExpectedOutcomesBlock(
                status=BlockStatus.INSUFFICIENT_DATA.value,
                phase=PHASE_DECISION,
                reason=reason,
            ),
            None,
        )

    routes = _routes_for(
        options_block.options,
        probabilities_by_company,
        net_by_label,
        inputs=inputs,
        thresholds=thresholds,
        batch_size=options_block.assumed_batch_size,
        gross_by_label=gross_by_label,
    )

    # Costing the card again at each tier's own minimum separates "not worth
    # grading" from "not worth grading on its own".
    batched: list[decision.RouteOutcome] | None = None
    if batch_size == 1:
        larger = _build_grading_options_block(
            db,
            card,
            settings_values,
            summary=summary,
            grade_block=grade_block,
            currency=currency,
            batch_size=_typical_batch(options_block.options),
        )
        if larger.assumed_batch_size > 1:
            batched = _routes_for(
                larger.options,
                probabilities_by_company,
                {
                    row.grade_label: to_minor(row.net) or 0
                    for row in larger.net_values
                    if row.net is not None
                },
                inputs=inputs,
                thresholds=thresholds,
                batch_size=larger.assumed_batch_size,
                gross_by_label={
                    row.grade_label: to_minor(row.gross) or 0
                    for row in larger.net_values
                    if row.gross is not None
                },
            )

    result = decision.decide(
        routes,
        inputs=inputs,
        thresholds=thresholds,
        batch_size=batch_size,
        routes_if_batched=batched,
    )

    priced = [route for route in routes if route.expected_profit_minor is not None]
    outcomes = [
        _outcome_out(route, currency)
        for route in sorted(
            priced,
            key=lambda item: (item.opportunity_score or 0, item.expected_profit_minor or 0),
            reverse=True,
        )
    ]

    if not priced:
        block = ExpectedOutcomesBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            phase=PHASE_DECISION,
            reason=(
                "No grader has sales data for the grades this card might get, so there is "
                "nothing to expect. Add graded comparables."
            ),
        )
    else:
        # Judged on the route the engine would actually recommend: one thinly
        # priced also-ran should not make a well-evidenced answer look shaky.
        leader = max(priced, key=lambda item: item.opportunity_score or 0)
        thin = leader.coverage < 0.8
        block = ExpectedOutcomesBlock(
            status=BlockStatus.PARTIAL.value if thin else BlockStatus.OK.value,
            reason=(
                f"Only {leader.coverage:.0%} of the likely grades have sales behind them, so "
                "the rest are left out of the expectation rather than counted as zero."
                if thin
                else None
            ),
            outcomes=outcomes,
        )
    return block, result


def _typical_batch(options: list[GradingOption]) -> int:
    """A batch size worth re-costing at: the largest minimum any tier asks for."""
    minimums = [option.minimum_cards for option in options if option.minimum_cards > 1]
    return max(minimums) if minimums else 1


def _routes_for(
    options: list[GradingOption],
    probabilities_by_company: dict[str, dict[float, float]],
    net_by_label: dict[str, int],
    *,
    inputs: decision.DecisionInputs,
    thresholds: decision.Thresholds,
    batch_size: int,
    gross_by_label: dict[str, int] | None = None,
) -> list[decision.RouteOutcome]:
    """One evaluated route per usable (company, tier)."""
    routes: list[decision.RouteOutcome] = []
    for option in options:
        if not option.available or option.total_cost is None:
            continue
        probabilities = probabilities_by_company.get(option.company_code)
        if not probabilities:
            continue
        routes.append(
            decision.evaluate_route(
                company_id=option.company_id,
                company_code=option.company_code,
                tier_id=option.tier_id,
                tier_name=option.tier_name,
                cost_minor=to_minor(option.total_cost) or 0,
                probabilities=probabilities,
                net_by_label=net_by_label,
                label_for=build_grade_label,
                inputs=inputs,
                thresholds=thresholds,
                batch_size=batch_size,
                gross_by_label=gross_by_label,
            )
        )
    return routes


def _outcome_out(route: decision.RouteOutcome, currency: str) -> ExpectedOutcome:
    return ExpectedOutcome(
        company_code=route.company_code,
        tier_name=route.tier_name,
        expected_gross=None,
        expected_net=to_major(route.expected_net_minor),
        expected_profit=to_major(route.expected_profit_minor),
        roi_pct=route.roi_pct,
        probability_of_profit=route.probability_of_profit,
        probability_of_target_profit=route.probability_of_target,
        minimum_profitable_grade=route.minimum_profitable_grade,
        downside=to_major(route.downside_minor),
        upside=to_major(route.upside_minor),
        liquidity_score=route.slab_liquidity,
        opportunity_score=route.opportunity_score,
        grading_cost=to_major(route.cost_minor),
        coverage=round(route.coverage, 4),
        confidence=route.confidence,
        score_parts=route.score_parts,
        probability_at_or_above_minimum=route.probability_at_or_above_minimum,
        notes=route.notes,
        rows=[
            OutcomeRow(
                grade=item.grade,
                label=item.label,
                probability=item.probability,
                gross_value=to_major(item.gross_minor),
                net_value=to_major(item.net_minor),
                profit=to_major(item.profit_minor),
            )
            for item in route.distribution.outcomes
        ],
    )


def _recommend(
    card: Card,
    blockers: list[str],
    explanation: list[ExplanationItem],
    *,
    result: decision.DecisionResult | None,
    raw_block: RawBlock,
    currency: str,
    batch_size: int,
) -> RecommendationBlock:
    """The verdict, with the numbers behind it and the route that lost.

    A decision the user set themselves always wins: the engine explains itself,
    it does not overrule them (spec section 35).
    """
    if card.decision_override:
        return RecommendationBlock(
            status=BlockStatus.OK.value,
            decision=card.decision_override,
            confidence=Confidence.NONE.value,
            headline=f"Set by you: {card.decision_override.replace('_', ' ').title()}",
            is_user_override=True,
            reasons=[
                ExplanationItem(
                    kind="info",
                    text="Your decision overrides the engine.",
                    detail=card.decision_override_reason or None,
                ),
                *explanation,
            ],
        )

    if result is None or result.decision == Decision.INSUFFICIENT_DATA.value:
        return RecommendationBlock(
            status=BlockStatus.INSUFFICIENT_DATA.value,
            phase=PHASE_DECISION,
            decision=Decision.INSUFFICIENT_DATA.value,
            confidence=Confidence.NONE.value,
            headline=(result.headline if result else "Not enough data to recommend a decision yet."),
            reason="; ".join(blockers) if blockers else None,
            reasons=explanation,
            net_raw_alternative=raw_block.net_raw_sale_value,
        )

    chosen = result.chosen
    reasons = [
        ExplanationItem(kind="info", text=text) for text in result.reasons
    ] + explanation

    return RecommendationBlock(
        status=BlockStatus.OK.value,
        decision=result.decision,
        confidence=result.confidence,
        headline=result.headline,
        company_code=chosen.company_code if chosen else None,
        tier_name=chosen.tier_name if chosen else None,
        expected_profit=to_major(chosen.expected_profit_minor) if chosen else None,
        expected_net=to_major(chosen.expected_net_minor) if chosen else None,
        net_raw_alternative=raw_block.net_raw_sale_value,
        roi_pct=chosen.roi_pct if chosen else None,
        probability_of_profit=chosen.probability_of_profit if chosen else None,
        probability_of_target_profit=chosen.probability_of_target if chosen else {},
        minimum_profitable_grade=chosen.minimum_profitable_grade if chosen else None,
        downside=to_major(chosen.downside_minor) if chosen else None,
        upside=to_major(chosen.upside_minor) if chosen else None,
        opportunity_score=chosen.opportunity_score if chosen else None,
        score_parts=chosen.score_parts if chosen else {},
        grading_cost=to_major(chosen.cost_minor) if chosen else None,
        # The batch the *quoted* numbers assume, which is not always the one
        # asked for: "worth grading, but not on its own" prices a fuller
        # submission, and saying 1 next to that cost would be a lie.
        assumed_batch_size=chosen.batch_size if chosen else batch_size,
        # Travels with the figures because below 1.0 they are conditional: an
        # expected profit computed over 13% of the outcomes is what you get *if*
        # the card lands on the one grade anybody has sold.
        coverage=chosen.coverage if chosen else 0.0,
        review_in_days=result.review_in_days,
        alternative=_outcome_out(result.alternative, currency) if result.alternative else None,
        alternative_note=result.alternative_note,
        reasons=reasons,
    )


def review_due(card: Card, today: date | None = None) -> bool:
    """Whether a card on hold is due to be looked at again (spec section 33)."""
    if card.review_after is None:
        return False
    return card.review_after <= (today or date.today())
