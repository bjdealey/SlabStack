"""How the model has actually done, and what it learned from that.

Two endpoints answering two different questions. `/analytics/accuracy` is the
report card — here is every prediction marked against what came back. `/calibration`
is the consequence — here is what that history is now doing to new predictions,
per grader, and whether it is being applied at all.

They are separate because the second is the one with teeth. A user who wants to
know why a number moved needs to see the correction on its own, not buried in a
page of statistics.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import GradingCompany
from app.schemas.common import ApiModel
from app.services import calibration as service

router = APIRouter(tags=["learning"])


# --- The report card ---------------------------------------------------------


class GradeBandOut(ApiModel):
    """One rung of the ladder: how often it was predicted, how often it landed."""

    grade: float
    predicted_count: float
    actual_count: int
    predicted_rate: float | None = None
    actual_rate: float | None = None
    gap_pct: float | None = Field(
        default=None,
        description="Actual minus predicted, in percentage points. Negative means the model "
        "predicts this grade more often than it happens.",
    )


class ScoredResultOut(ApiModel):
    card_id: str
    name: str
    company_code: str | None = None
    predicted_grade: float | None = None
    actual_grade: float
    surprise: float | None = Field(
        default=None, description="Actual minus predicted. Positive graded better than expected."
    )
    brier: float | None = Field(
        default=None, description="How wrong the whole distribution was. Lower is better."
    )
    graded_at: str | None = None


class CompanyAccuracyOut(ApiModel):
    company_id: str
    company_code: str
    company_name: str
    scored: int = 0
    exact_pct: float | None = None
    within_half_pct: float | None = None
    within_one_pct: float | None = None
    mean_error: float | None = Field(
        default=None,
        description="Mean signed error in grades — the bias. Positive means cards come back "
        "better than predicted, so your assessment reads harsh.",
    )
    mean_absolute_error: float | None = None
    error_stdev: float | None = None
    mean_brier: float | None = None
    bands: list[GradeBandOut] = Field(default_factory=list)
    headline: str | None = None
    status: str
    reason: str | None = None


class AccuracyOut(ApiModel):
    status: str
    reason: str | None = None
    scored: int = 0
    awaiting: int = Field(
        default=0,
        description="Graded cards with no prediction recorded when they were sent, so they "
        "cannot be marked. Counted rather than dropped.",
    )
    minimum_sample: int = 0
    companies: list[CompanyAccuracyOut] = Field(default_factory=list)
    results: list[ScoredResultOut] = Field(default_factory=list)


@router.get(
    "/analytics/accuracy",
    response_model=AccuracyOut,
    summary="Predicted vs actual grading accuracy",
    description=(
        "Every recorded result, marked. The distribution being scored is the one frozen onto "
        "the submission line when the card was sent — scoring one recomputed afterwards would "
        "mark the model against an outcome it has already seen, which measures nothing.\n\n"
        "`mean_error` is the bias and is signed: positive means cards come back better than "
        "predicted. `bands` is the calibration curve — a model that says '40% a 10' across "
        "fifty cards should see roughly twenty.\n\n"
        "Graded cards sent before a prediction was ever recorded appear in `awaiting`, not in "
        "the denominator."
    ),
)
def accuracy(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> AccuracyOut:
    result = service.report(db, limit=limit)
    return AccuracyOut(
        status=result.status,
        reason=result.reason,
        scored=result.scored,
        awaiting=result.awaiting,
        minimum_sample=result.minimum_sample,
        companies=[
            CompanyAccuracyOut(
                **{key: value for key, value in vars(row).items() if key != "bands"},
                bands=[GradeBandOut(**vars(band)) for band in row.bands],
            )
            for row in result.companies
        ],
        results=[ScoredResultOut(**vars(row)) for row in result.results],
    )


# --- The consequence ---------------------------------------------------------


class CalibrationOut(ApiModel):
    company_id: str
    company_code: str
    sample_size: int = 0
    minimum_sample: int = 0
    grade_offset: float = Field(
        default=0.0,
        description="Grades added to the model's centre. Positive means it reads harsh. "
        "Reported whether or not it is applied.",
    )
    spread_multiplier: float = Field(
        default=1.0, description="Multiplier on the range. Above 1.0 the model was over-confident."
    )
    applied: bool = Field(
        default=False,
        description="False below the minimum sample or when calibration is switched off. The "
        "numbers above are still measured — they are simply not acting on anything.",
    )
    confidence: str
    reason: str | None = None


class CalibrationStateOut(ApiModel):
    enabled: bool
    minimum_sample: int
    max_offset: float
    companies: list[CalibrationOut] = Field(default_factory=list)


@router.get(
    "/calibration",
    response_model=CalibrationStateOut,
    summary="What your results have taught the model",
    description=(
        "Per grading company, because PSA's bias is not CGC's — a correction learned across "
        "both describes neither.\n\n"
        "A correction is measured from the first result and **applied** only past "
        "`minimum_sample`. Below that it is reported with `applied: false`: a bias fitted to "
        "four slabs is fitted to noise, and silently correcting for noise makes the model worse "
        "without saying so. It is also clamped, because a measured two-grade bias is far more "
        "likely to be a run of odd cards than a real one.\n\n"
        "Kept separate from the per-company `strictness` setting. That is an opinion you set "
        "about a grader; this is an observation measured from your results, and merging them "
        "would lose the ability to tell them apart."
    ),
)
def calibration_state(db: DbSession) -> CalibrationStateOut:
    from app.services import settings_service

    values = settings_service.get_all(db)
    companies = list(
        db.scalars(
            select(GradingCompany)
            .where(GradingCompany.active.is_(True))
            .order_by(GradingCompany.sort_order, GradingCompany.code)
        )
    )
    return CalibrationStateOut(
        enabled=bool(values.get("calibration_enabled", True)),
        minimum_sample=int(
            values.get("calibration_minimum_sample", service.DEFAULT_MINIMUM_SAMPLE)
        ),
        max_offset=float(values.get("calibration_max_offset", service.DEFAULT_MAX_OFFSET)),
        companies=[
            CalibrationOut(**vars(service.calibration_for(db, company))) for company in companies
        ],
    )
