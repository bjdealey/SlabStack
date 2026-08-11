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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import (
    DEFECT_FIELDS,
    BlockStatus,
    Confidence,
    Decision,
    PredictionKind,
    PredictionSource,
    Severity,
)
from app.models import Card, GradingCompany, MarketPrice, MarketSale
from app.money import to_major
from app.schemas.evaluation import (
    ENGINE_VERSION,
    CardEvaluation,
    CompanyGradePrediction,
    ConditionBlock,
    ConditionScoreOut,
    ExpectedOutcomesBlock,
    ExplanationItem,
    GradePredictionBlock,
    GradeProbability,
    GradingOption,
    GradingOptionsBlock,
    LiquidityBlock,
    MarketBlock,
    RawBlock,
    RecommendationBlock,
    TrendBlock,
)
from app.services import (
    cards_service,
    condition_service,
    prediction_service,
    predictions,
    settings_service,
)
from app.services.prediction_service import ModelParameters, NotEnoughAssessmentError

PHASE_GRADE_PREDICTION = 2
PHASE_MARKET = 3
PHASE_ECONOMICS = 4
PHASE_DECISION = 5

_NOT_ASSESSED_REASON = "No condition assessment recorded yet."
_NO_MARKET_REASON = (
    "No market data for this card yet. Add sales manually or enable a data source."
)

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


def _build_raw_block(card: Card, currency: str, market_raw: MarketPrice | None) -> RawBlock:
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
        # Needs the selling-cost engine, which lands with the economics phase.
        net_raw_sale_value=None,
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


def _build_grading_options_block(db: Session, card: Card, settings_values: dict) -> GradingOptionsBlock:
    """Which grading routes exist at all, from configuration alone.

    Costing an option needs a declared value, which needs market data — so
    Phase 1 reports availability and blockers, not prices.
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

    options: list[GradingOption] = []
    for company in companies:
        priced_tiers = [tier for tier in company.tiers if tier.active and tier.price_minor > 0]
        if not priced_tiers:
            options.append(
                GradingOption(
                    company_id=company.id,
                    company_code=company.code,
                    company_name=company.name,
                    currency=company.currency,
                    available=False,
                    blockers=[
                        f"No priced tier configured for {company.code}. "
                        "Add current pricing in Settings → Grading."
                    ],
                )
            )
            continue

        for tier in sorted(priced_tiers, key=lambda t: (t.sort_order, t.price_minor)):
            options.append(
                GradingOption(
                    company_id=company.id,
                    company_code=company.code,
                    company_name=company.name,
                    tier_id=tier.id,
                    tier_name=tier.tier_name,
                    currency=tier.currency,
                    grading_fee=to_major(tier.price_minor),
                    turnaround_days=tier.turnaround_days,
                    minimum_cards=tier.minimum_cards,
                    requires_batch=tier.minimum_cards > 1,
                    membership_required=tier.membership_required,
                    available=True,
                    blockers=[],
                )
            )

    status = BlockStatus.PARTIAL.value if options else BlockStatus.INSUFFICIENT_DATA.value
    return GradingOptionsBlock(
        status=status,
        phase=PHASE_ECONOMICS,
        reason=(
            "Tier availability only. Declared value, batch allocation and total cost per card "
            "arrive with the grading-economics engine."
            if options
            else "No active grading company with a priced tier is configured."
        ),
        options=options,
    )


def _market_rows(db: Session, catalog_key: str | None) -> tuple[MarketPrice | None, int]:
    if not catalog_key:
        return None, 0
    raw_price = db.scalars(
        select(MarketPrice).where(
            MarketPrice.catalog_key == catalog_key, MarketPrice.grade_label == "raw"
        )
    ).first()
    sale_count = (
        db.scalar(
            select(func.count())
            .select_from(MarketSale)
            .where(MarketSale.catalog_key == catalog_key, MarketSale.is_excluded.is_(False))
        )
        or 0
    )
    return raw_price, sale_count


def evaluate_card(db: Session, card: Card) -> CardEvaluation:
    settings_values = settings_service.get_all(db)
    currency = settings_values.get("currency", "GBP")

    raw_price, sale_count = _market_rows(db, card.catalog_key)

    raw_block = _build_raw_block(card, currency, raw_price)
    condition_block = _build_condition_block(db, card)
    options_block = _build_grading_options_block(db, card, settings_values)

    grade_block = _build_grade_prediction_block(db, card, settings_values)

    market_block = MarketBlock(
        status=BlockStatus.INSUFFICIENT_DATA.value,
        phase=PHASE_MARKET,
        reason=_NO_MARKET_REASON if sale_count == 0 else "Valuation engine not built yet.",
        currency=currency,
    )
    liquidity_block = LiquidityBlock(
        status=BlockStatus.INSUFFICIENT_DATA.value,
        phase=PHASE_MARKET,
        reason="Liquidity needs sales history.",
    )
    trend_block = TrendBlock(
        status=BlockStatus.INSUFFICIENT_DATA.value,
        phase=PHASE_MARKET,
        reason="A trend needs a series of sales, not a single price.",
    )
    outcomes_block = ExpectedOutcomesBlock(
        status=BlockStatus.NOT_IMPLEMENTED.value,
        phase=PHASE_DECISION,
        reason="Expected value needs grade probabilities and graded market prices.",
    )

    explanation, blockers = _explain(
        card, condition_block, grade_block, options_block, sale_count
    )
    recommendation = _recommend(card, blockers, explanation)

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
        data_confidence=Confidence.NONE.value,
    )


def _explain(
    card: Card,
    condition_block: ConditionBlock,
    grade_block: GradePredictionBlock,
    options_block: GradingOptionsBlock,
    sale_count: int,
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
                f"({grade_block.confidence} confidence).",
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

    if sale_count:
        items.append(
            ExplanationItem(kind="pass", text=f"{sale_count} comparable sale(s) stored locally.")
        )
    else:
        items.append(ExplanationItem(kind="fail", text="No market data for this card."))
    blockers.append("Add comparable sales for the raw card and each relevant grade.")

    available = [option for option in options_block.options if option.available]
    if available:
        companies = sorted({option.company_code for option in available})
        items.append(
            ExplanationItem(
                kind="pass",
                text=f"Grading tiers configured for {', '.join(companies)}.",
                detail=f"{len(available)} priced tier(s) available.",
            )
        )
    else:
        items.append(ExplanationItem(kind="fail", text="No priced grading tier configured."))
        blockers.append("Enter current pricing for at least one grading company.")

    if card.user_raw_value_minor is None and card.purchase_price_minor is None:
        items.append(
            ExplanationItem(
                kind="info",
                text="No raw value recorded.",
                detail="A purchase price or your own raw estimate gives the engine a floor to beat.",
            )
        )

    return items, blockers


def _recommend(
    card: Card, blockers: list[str], explanation: list[ExplanationItem]
) -> RecommendationBlock:
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

    return RecommendationBlock(
        status=BlockStatus.INSUFFICIENT_DATA.value,
        phase=PHASE_DECISION,
        decision=Decision.INSUFFICIENT_DATA.value,
        confidence=Confidence.NONE.value,
        headline="Not enough data to recommend a decision yet.",
        reason="; ".join(blockers) if blockers else None,
        reasons=explanation,
    )


def review_due(card: Card, today: date | None = None) -> bool:
    """Whether a card on hold is due to be looked at again (spec section 33)."""
    if card.review_after is None:
        return False
    return card.review_after <= (today or date.today())
