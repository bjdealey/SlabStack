"""Condition assessment, grade prediction, and prediction outcome tracking.

Spec section 6 is explicit that condition must not collapse to NM/LP/MP, so the
assessment is a wide, structured row: every defect class is recorded per face at
``none|minor|moderate|severe``. Free-text notes per defect live in the
``*_defect_notes`` JSON maps (field name -> note) rather than 32 more columns.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import Assessor, Confidence, PredictionKind, PredictionSource, Severity
from app.models.base import Base, TimestampMixin, enum_check, pk_column, utcnow


def _severity() -> Mapped[str]:
    return mapped_column(String(16), nullable=False, default=Severity.UNKNOWN.value)


class ConditionAssessment(Base, TimestampMixin):
    __tablename__ = "condition_assessments"
    __table_args__ = (
        enum_check("assessor", Assessor),
        Index("ix_condition_card_current", "card_id", "is_current"),
    )

    id: Mapped[str] = pk_column()
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    assessor: Mapped[str] = mapped_column(String(24), nullable=False, default=Assessor.USER.value)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Centering, entered as border percentages (spec section 7) ----------
    front_centering_left: Mapped[float | None] = mapped_column(Float)
    front_centering_right: Mapped[float | None] = mapped_column(Float)
    front_centering_top: Mapped[float | None] = mapped_column(Float)
    front_centering_bottom: Mapped[float | None] = mapped_column(Float)
    back_centering_left: Mapped[float | None] = mapped_column(Float)
    back_centering_right: Mapped[float | None] = mapped_column(Float)
    back_centering_top: Mapped[float | None] = mapped_column(Float)
    back_centering_bottom: Mapped[float | None] = mapped_column(Float)

    # --- Front defects ------------------------------------------------------
    front_corner_tl: Mapped[str] = _severity()
    front_corner_tr: Mapped[str] = _severity()
    front_corner_bl: Mapped[str] = _severity()
    front_corner_br: Mapped[str] = _severity()
    front_edge_condition: Mapped[str] = _severity()
    front_surface_condition: Mapped[str] = _severity()
    front_holo_condition: Mapped[str] = _severity()
    front_scratches: Mapped[str] = _severity()
    front_print_lines: Mapped[str] = _severity()
    front_silvering: Mapped[str] = _severity()
    front_whitening: Mapped[str] = _severity()
    front_dents: Mapped[str] = _severity()
    front_dimpling: Mapped[str] = _severity()
    front_creases: Mapped[str] = _severity()
    front_staining: Mapped[str] = _severity()
    front_misc_defects: Mapped[str] = _severity()

    # --- Back defects -------------------------------------------------------
    back_corner_tl: Mapped[str] = _severity()
    back_corner_tr: Mapped[str] = _severity()
    back_corner_bl: Mapped[str] = _severity()
    back_corner_br: Mapped[str] = _severity()
    back_edge_condition: Mapped[str] = _severity()
    back_surface_condition: Mapped[str] = _severity()
    back_holo_condition: Mapped[str] = _severity()
    back_scratches: Mapped[str] = _severity()
    back_print_lines: Mapped[str] = _severity()
    back_silvering: Mapped[str] = _severity()
    back_whitening: Mapped[str] = _severity()
    back_dents: Mapped[str] = _severity()
    back_dimpling: Mapped[str] = _severity()
    back_creases: Mapped[str] = _severity()
    back_staining: Mapped[str] = _severity()
    back_misc_defects: Mapped[str] = _severity()

    front_defect_notes: Mapped[dict | None] = mapped_column(JSON)
    back_defect_notes: Mapped[dict | None] = mapped_column(JSON)
    front_notes: Mapped[str | None] = mapped_column(Text)
    back_notes: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    # --- Derived sub-scores, 0-10 (recomputed on every write) ---------------
    centering_score_front: Mapped[float | None] = mapped_column(Float)
    centering_score_back: Mapped[float | None] = mapped_column(Float)
    centering_score: Mapped[float | None] = mapped_column(Float)
    corners_score: Mapped[float | None] = mapped_column(Float)
    edges_score: Mapped[float | None] = mapped_column(Float)
    surface_score: Mapped[float | None] = mapped_column(Float)
    completeness: Mapped[float | None] = mapped_column(Float)  # fraction of fields answered


class GradePrediction(Base):
    """A probability distribution over grades.

    ``kind`` keeps the two questions in spec section 8 separate: what the card
    physically is, versus what a given grader is likely to award it.
    ``company_id`` is NULL for a generic (company-agnostic) prediction.
    """

    __tablename__ = "grade_predictions"
    __table_args__ = (
        enum_check("kind", PredictionKind),
        enum_check("source", PredictionSource),
        enum_check("confidence", Confidence),
        Index("ix_grade_predictions_card_current", "card_id", "is_current"),
    )

    id: Mapped[str] = pk_column()
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    condition_assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("condition_assessments.id", ondelete="SET NULL")
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=PredictionKind.MARKET.value)
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PredictionSource.RULES_ENGINE.value
    )
    model_version: Mapped[str | None] = mapped_column(String(32))

    # {"10": 0.55, "9": 0.38, "8": 0.07} — keys are grade strings, values sum to 1.
    probabilities: Mapped[dict] = mapped_column(JSON, nullable=False)
    likely_grade: Mapped[float | None] = mapped_column(Float)
    grade_min: Mapped[float | None] = mapped_column(Float)
    grade_max: Mapped[float | None] = mapped_column(Float)
    max_grade_cap: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default=Confidence.LOW.value)
    caps_applied: Mapped[list | None] = mapped_column(JSON)
    explanation: Mapped[list | None] = mapped_column(JSON)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class GradeRule(Base, TimestampMixin):
    """Configurable defect caps and probability adjustments (spec section 9).

    These are *our* estimates, not published grader standards, so they live in
    the database where the user can tune them — never hard-coded.
    """

    __tablename__ = "grade_rules"

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="CASCADE")
    )
    # Which assessment field and severity threshold triggers the rule.
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    face: Mapped[str | None] = mapped_column(String(8))  # front | back | any
    min_severity: Mapped[str] = mapped_column(String(16), nullable=False, default=Severity.MINOR.value)
    # Effect: hard cap on the achievable grade, and/or a multiplier on the
    # probability mass of grades at or above `penalty_from_grade`.
    max_grade: Mapped[float | None] = mapped_column(Float)
    probability_multiplier: Mapped[float | None] = mapped_column(Float)
    penalty_from_grade: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class PredictionResult(Base):
    """Predicted vs actual, the input to the Phase 8 calibration loop."""

    __tablename__ = "prediction_results"

    id: Mapped[str] = pk_column()
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grade_prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("grade_predictions.id", ondelete="SET NULL")
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_submissions.id", ondelete="SET NULL")
    )
    actual_grade: Mapped[float] = mapped_column(Float, nullable=False)
    actual_subgrades: Mapped[dict | None] = mapped_column(JSON)
    graded_at: Mapped[date | None] = mapped_column(Date)
    cert_number: Mapped[str | None] = mapped_column(String(64))
    predicted_probabilities: Mapped[dict | None] = mapped_column(JSON)
    predicted_likely_grade: Mapped[float | None] = mapped_column(Float)
    brier_score: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
