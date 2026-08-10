"""Condition assessment and grade prediction.

Storage and scoring are Phase 1; the probability model is Phase 2 and returns a
501 with the phase attached rather than a guess.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CardDep, DbSession
from app.api.errors import NotFoundError, NotImplementedYetError
from app.models import ConditionAssessment, GradePrediction, GradingCompany
from app.schemas.condition import (
    ConditionAssessmentOut,
    ConditionAssessmentWrite,
    GradePredictionOut,
)
from app.services import cards_service, condition_service

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


@router.get(
    "/cards/{card_id}/grade-predictions",
    response_model=list[GradePredictionOut],
    summary="Stored grade predictions for a card",
)
def list_predictions(db: DbSession, card: CardDep) -> list[GradePredictionOut]:
    rows = list(
        db.scalars(
            select(GradePrediction)
            .where(GradePrediction.card_id == card.id)
            .order_by(GradePrediction.created_at.desc())
        )
    )
    codes = {
        company.id: company.code
        for company in db.scalars(select(GradingCompany))
    }
    return [GradePredictionOut.from_model(row, codes.get(row.company_id or "")) for row in rows]


@router.post(
    "/cards/{card_id}/grade-prediction",
    response_model=GradePredictionOut,
    summary="Run the grade probability model (Phase 2)",
    responses={501: {"description": "The rules engine has not been built yet."}},
)
def run_prediction(card: CardDep) -> GradePredictionOut:
    raise NotImplementedYetError(
        "The grade probability model is not built yet. Condition assessments are being stored "
        "and scored now, so predictions can be generated for every card the moment it lands.",
        phase=2,
        planned_in="Phase 2 — condition engine: configurable defect caps plus probability model.",
    )
