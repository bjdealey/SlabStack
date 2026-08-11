"""Generating and storing grade predictions for a card.

Thin orchestration around ``prediction_service``: work out which companies to
predict for, run the model, and persist the results as the current set. The
model itself is pure — it takes an assessment and returns a distribution — which
keeps it testable without a database.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import PredictionKind, PredictionSource
from app.models import Card, ConditionAssessment, GradePrediction, GradingCompany
from app.services import cards_service, prediction_service, settings_service
from app.services.prediction_service import (
    MODEL_VERSION,
    ModelParameters,
    NotEnoughAssessmentError,
    Prediction,
)

__all__ = [
    "MODEL_VERSION",
    "NotEnoughAssessmentError",
    "companies_for_prediction",
    "current_predictions",
    "generate_for_card",
    "store_override",
]


def companies_for_prediction(db: Session, settings_values: dict) -> list[GradingCompany]:
    """Active companies the user has asked the engine to consider."""
    wanted = settings_values.get("default_grading_company_codes") or []
    companies = list(
        db.scalars(
            select(GradingCompany)
            .where(GradingCompany.active.is_(True))
            .order_by(GradingCompany.sort_order, GradingCompany.code)
        )
    )
    if wanted:
        filtered = [company for company in companies if company.code in wanted]
        return filtered or companies
    return companies


def _persist(
    db: Session,
    card: Card,
    assessment: ConditionAssessment,
    prediction: Prediction,
    *,
    company: GradingCompany | None,
    kind: str,
    source: str = PredictionSource.RULES_ENGINE.value,
) -> GradePrediction:
    row = GradePrediction(
        card_id=card.id,
        condition_assessment_id=assessment.id,
        company_id=company.id if company else None,
        kind=kind,
        source=source,
        model_version=prediction.model_version,
        probabilities=prediction.probabilities,
        likely_grade=prediction.likely_grade,
        grade_min=prediction.grade_min,
        grade_max=prediction.grade_max,
        max_grade_cap=prediction.max_grade_cap,
        confidence=prediction.confidence,
        caps_applied=prediction.caps_applied,
        explanation=prediction.explanation,
        is_current=True,
    )
    db.add(row)
    return row


def _retire_current(db: Session, card_id: str, *, keep_overrides: bool = True) -> None:
    """Mark existing predictions superseded.

    User overrides survive by default: a rerun of the model should not silently
    discard a number the user entered deliberately (spec section 35).
    """
    stmt = select(GradePrediction).where(
        GradePrediction.card_id == card_id, GradePrediction.is_current.is_(True)
    )
    if keep_overrides:
        stmt = stmt.where(GradePrediction.source != PredictionSource.USER_OVERRIDE.value)
    for row in db.scalars(stmt):
        row.is_current = False


def generate_for_card(db: Session, card: Card) -> list[GradePrediction]:
    """Run the model for a card against every company in scope.

    Produces one company-agnostic ``physical`` prediction — what the card is —
    plus one ``market`` prediction per company — what that grader is likely to
    award it. Spec section 8 keeps them apart.
    """
    assessment = cards_service.current_condition(db, card.id)
    if assessment is None:
        raise NotEnoughAssessmentError(
            "This card has no condition assessment yet, so there is nothing to predict from."
        )

    settings_values = settings_service.get_all(db)
    params = ModelParameters.from_settings(settings_values)

    generic_rules = prediction_service.load_rules(db, None)
    physical = prediction_service.predict(
        assessment,
        company=None,
        rules=generic_rules,
        params=params,
        kind=PredictionKind.PHYSICAL.value,
    )

    _retire_current(db, card.id)
    stored = [
        _persist(
            db, card, assessment, physical, company=None, kind=PredictionKind.PHYSICAL.value
        )
    ]

    for company in companies_for_prediction(db, settings_values):
        rules = prediction_service.load_rules(db, company.id)
        market = prediction_service.predict(
            assessment,
            company=company,
            rules=rules,
            params=params,
            kind=PredictionKind.MARKET.value,
        )
        stored.append(
            _persist(
                db, card, assessment, market, company=company, kind=PredictionKind.MARKET.value
            )
        )

    db.flush()
    return stored


def current_predictions(db: Session, card_id: str) -> list[GradePrediction]:
    return list(
        db.scalars(
            select(GradePrediction)
            .where(GradePrediction.card_id == card_id, GradePrediction.is_current.is_(True))
            .order_by(GradePrediction.kind, GradePrediction.created_at.desc())
        )
    )


def store_override(
    db: Session,
    card: Card,
    *,
    company: GradingCompany | None,
    probabilities: dict[str, float],
    confidence: str,
    notes: str | None = None,
) -> GradePrediction:
    """Record the user's own distribution, superseding the model's for that company.

    The model's own output is kept as a superseded row rather than deleted, so
    Phase 8 can still score what the model said against what actually happened.
    """
    assessment = cards_service.current_condition(db, card.id)

    company_id = company.id if company else None
    for row in db.scalars(
        select(GradePrediction).where(
            GradePrediction.card_id == card.id,
            GradePrediction.is_current.is_(True),
            GradePrediction.kind == PredictionKind.MARKET.value,
        )
    ):
        if row.company_id == company_id:
            row.is_current = False

    grades = sorted((float(grade) for grade in probabilities), reverse=True)
    likely = max(probabilities, key=lambda grade: probabilities[grade])

    row = GradePrediction(
        card_id=card.id,
        condition_assessment_id=assessment.id if assessment else None,
        company_id=company_id,
        kind=PredictionKind.MARKET.value,
        source=PredictionSource.USER_OVERRIDE.value,
        model_version=MODEL_VERSION,
        probabilities={str(key): float(value) for key, value in probabilities.items()},
        likely_grade=float(likely),
        grade_min=min(grades),
        grade_max=max(grades),
        confidence=confidence,
        caps_applied=[],
        explanation=[
            {
                "kind": "info",
                "text": "You set these probabilities yourself.",
                "detail": notes,
            }
        ],
        is_current=True,
    )
    db.add(row)
    db.flush()
    return row
