"""Condition assessment and grade prediction.

Assessments are stored and scored here; the probability model that reads them
lives in ``app.services.prediction_service``.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CardDep, DbSession
from app.api.errors import ApiError, NotFoundError
from app.models import ConditionAssessment, GradePrediction, GradingCompany
from app.schemas.condition import (
    ConditionAssessmentOut,
    ConditionAssessmentWrite,
    GradePredictionOut,
    GradePredictionOverride,
)
from app.services import cards_service, condition_service, predictions

router = APIRouter(tags=["condition"])


@router.get(
    "/cards/{card_id}/condition",
    response_model=ConditionAssessmentOut,
    summary="Get the current condition assessment",
)
def get_condition(db: DbSession, card: CardDep) -> ConditionAssessmentOut:
    assessment = cards_service.current_condition(db, card.id)
    if assessment is None:
        raise NotFoundError("Condition assessment for card", card.id)
    return ConditionAssessmentOut.from_model(
        assessment, condition_service.overall_condition_score(assessment)
    )


@router.put(
    "/cards/{card_id}/condition",
    response_model=ConditionAssessmentOut,
    status_code=status.HTTP_200_OK,
    summary="Record a condition assessment",
    description=(
        "Creates a new assessment and marks it current. Previous assessments are kept: "
        "a card re-examined under better light is a new opinion, not a correction, and the "
        "history is what the Phase 8 calibration loop learns from."
    ),
)
def put_condition(
    db: DbSession, card: CardDep, payload: ConditionAssessmentWrite
) -> ConditionAssessmentOut:
    previous = db.scalars(
        select(ConditionAssessment).where(
            ConditionAssessment.card_id == card.id, ConditionAssessment.is_current.is_(True)
        )
    ).all()
    for row in previous:
        row.is_current = False

    assessment = ConditionAssessment(card_id=card.id, is_current=True)
    payload.apply_to(assessment)
    condition_service.recompute_scores(assessment)
    db.add(assessment)
    db.flush()
    return ConditionAssessmentOut.from_model(
        assessment, condition_service.overall_condition_score(assessment)
    )


@router.get(
    "/cards/{card_id}/condition/history",
    response_model=list[ConditionAssessmentOut],
    summary="All assessments recorded for a card",
)
def condition_history(db: DbSession, card: CardDep) -> list[ConditionAssessmentOut]:
    rows = db.scalars(
        select(ConditionAssessment)
        .where(ConditionAssessment.card_id == card.id)
        .order_by(ConditionAssessment.assessed_at.desc())
    )
    return [
        ConditionAssessmentOut.from_model(row, condition_service.overall_condition_score(row))
        for row in rows
    ]


def _company_codes(db: DbSession) -> dict[str, str]:
    return {company.id: company.code for company in db.scalars(select(GradingCompany))}


@router.get(
    "/cards/{card_id}/grade-predictions",
    response_model=list[GradePredictionOut],
    summary="Stored grade predictions for a card",
)
def list_predictions(
    db: DbSession, card: CardDep, current_only: bool = False
) -> list[GradePredictionOut]:
    stmt = select(GradePrediction).where(GradePrediction.card_id == card.id)
    if current_only:
        stmt = stmt.where(GradePrediction.is_current.is_(True))
    rows = list(db.scalars(stmt.order_by(GradePrediction.created_at.desc())))
    codes = _company_codes(db)
    return [GradePredictionOut.from_model(row, codes.get(row.company_id or "")) for row in rows]


@router.post(
    "/cards/{card_id}/grade-prediction",
    response_model=list[GradePredictionOut],
    summary="Run the grade probability model",
    description=(
        "Produces one company-agnostic `physical` prediction — what the card is — plus one "
        "`market` prediction per grading company in scope. Supersedes the previous run, but "
        "leaves any prediction you overrode yourself alone."
    ),
)
def run_prediction(db: DbSession, card: CardDep) -> list[GradePredictionOut]:
    try:
        rows = predictions.generate_for_card(db, card)
    except predictions.NotEnoughAssessmentError as exc:
        raise ApiError("no_assessment", str(exc)) from exc
    codes = _company_codes(db)
    return [GradePredictionOut.from_model(row, codes.get(row.company_id or "")) for row in rows]


@router.put(
    "/cards/{card_id}/grade-prediction/override",
    response_model=GradePredictionOut,
    summary="Set grade probabilities yourself",
    description=(
        "Overrides the model for one company. The model's own output is superseded rather "
        "than deleted, so its accuracy can still be scored later against the actual grade."
    ),
)
def override_prediction(
    db: DbSession, card: CardDep, payload: GradePredictionOverride
) -> GradePredictionOut:
    company = None
    if payload.company_id:
        company = db.get(GradingCompany, payload.company_id)
        if company is None:
            raise NotFoundError("Grading company", payload.company_id)

    row = predictions.store_override(
        db,
        card,
        company=company,
        probabilities=payload.probabilities,
        confidence=payload.confidence,
        notes=payload.notes,
    )
    return GradePredictionOut.from_model(row, company.code if company else None)
