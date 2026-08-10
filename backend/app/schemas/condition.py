"""Condition assessment payloads.

The wire format is nested (``front`` / ``back`` / ``centering``) even though the
table is flat, because that is how the card is actually inspected and how the UI
lays the form out. Flattening happens in one place, here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.enums import DEFECT_FIELDS, Assessor, Confidence, PredictionKind, Severity
from app.models import ConditionAssessment, GradePrediction
from app.schemas.common import ApiModel

_UNKNOWN = Severity.UNKNOWN.value


class FaceDefects(ApiModel):
    corner_tl: str = _UNKNOWN
    corner_tr: str = _UNKNOWN
    corner_bl: str = _UNKNOWN
    corner_br: str = _UNKNOWN
    edge_condition: str = _UNKNOWN
    surface_condition: str = _UNKNOWN
    holo_condition: str = _UNKNOWN
    scratches: str = _UNKNOWN
    print_lines: str = _UNKNOWN
    silvering: str = _UNKNOWN
    whitening: str = _UNKNOWN
    dents: str = _UNKNOWN
    dimpling: str = _UNKNOWN
    creases: str = _UNKNOWN
    staining: str = _UNKNOWN
    misc_defects: str = _UNKNOWN
    notes: str | None = None
    defect_notes: dict[str, str] | None = Field(
        default=None, description="Per-defect free text, keyed by defect field name."
    )

    @field_validator(*DEFECT_FIELDS)
    @classmethod
    def _valid_severity(cls, value: str) -> str:
        if value not in Severity.values():
            raise ValueError(f"severity must be one of {Severity.values()}")
        return value

    @field_validator("defect_notes")
    @classmethod
    def _known_fields(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value:
            unknown = set(value) - set(DEFECT_FIELDS)
            if unknown:
                raise ValueError(f"unknown defect fields: {sorted(unknown)}")
        return value


class Centering(ApiModel):
    """Border measurements. Percentages are conventional but any consistent unit
    works — the score uses each pair's ratio, not its absolute size."""

    left: float | None = Field(default=None, ge=0, le=100)
    right: float | None = Field(default=None, ge=0, le=100)
    top: float | None = Field(default=None, ge=0, le=100)
    bottom: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _pairs_complete(self) -> Centering:
        for a, b, axis in ((self.left, self.right, "left/right"), (self.top, self.bottom, "top/bottom")):
            if (a is None) != (b is None):
                raise ValueError(f"centering {axis} must be given as a pair")
        return self


class CenteringPair(ApiModel):
    front: Centering = Field(default_factory=Centering)
    back: Centering = Field(default_factory=Centering)


class ConditionAssessmentWrite(ApiModel):
    assessor: str = Assessor.USER.value
    centering: CenteringPair = Field(default_factory=CenteringPair)
    front: FaceDefects = Field(default_factory=FaceDefects)
    back: FaceDefects = Field(default_factory=FaceDefects)
    notes: str | None = None

    @field_validator("assessor")
    @classmethod
    def _valid_assessor(cls, value: str) -> str:
        if value not in Assessor.values():
            raise ValueError(f"assessor must be one of {Assessor.values()}")
        return value

    def apply_to(self, assessment: ConditionAssessment) -> ConditionAssessment:
        assessment.assessor = self.assessor
        assessment.notes = self.notes
        for face_name, face in (("front", self.front), ("back", self.back)):
            for defect in DEFECT_FIELDS:
                setattr(assessment, f"{face_name}_{defect}", getattr(face, defect))
            setattr(assessment, f"{face_name}_notes", face.notes)
            setattr(assessment, f"{face_name}_defect_notes", face.defect_notes)
        for face_name in ("front", "back"):
            centering: Centering = getattr(self.centering, face_name)
            for edge in ("left", "right", "top", "bottom"):
                setattr(assessment, f"{face_name}_centering_{edge}", getattr(centering, edge))
        return assessment


class ConditionScores(ApiModel):
    centering: float | None = None
    centering_front: float | None = None
    centering_back: float | None = None
    corners: float | None = None
    edges: float | None = None
    surface: float | None = None
    overall: float | None = None
    completeness: float | None = None


class ConditionAssessmentOut(ApiModel):
    id: str
    card_id: str
    assessed_at: datetime
    assessor: str
    is_current: bool
    centering: CenteringPair
    front: FaceDefects
    back: FaceDefects
    notes: str | None
    scores: ConditionScores
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(
        cls, assessment: ConditionAssessment, overall: float | None = None
    ) -> ConditionAssessmentOut:
        def face(prefix: str) -> FaceDefects:
            values: dict[str, Any] = {
                defect: getattr(assessment, f"{prefix}_{defect}") for defect in DEFECT_FIELDS
            }
            values["notes"] = getattr(assessment, f"{prefix}_notes")
            values["defect_notes"] = getattr(assessment, f"{prefix}_defect_notes")
            return FaceDefects(**values)

        def centering(prefix: str) -> Centering:
            return Centering(
                left=getattr(assessment, f"{prefix}_centering_left"),
                right=getattr(assessment, f"{prefix}_centering_right"),
                top=getattr(assessment, f"{prefix}_centering_top"),
                bottom=getattr(assessment, f"{prefix}_centering_bottom"),
            )

        return cls(
            id=assessment.id,
            card_id=assessment.card_id,
            assessed_at=assessment.assessed_at,
            assessor=assessment.assessor,
            is_current=assessment.is_current,
            centering=CenteringPair(front=centering("front"), back=centering("back")),
            front=face("front"),
            back=face("back"),
            notes=assessment.notes,
            scores=ConditionScores(
                centering=assessment.centering_score,
                centering_front=assessment.centering_score_front,
                centering_back=assessment.centering_score_back,
                corners=assessment.corners_score,
                edges=assessment.edges_score,
                surface=assessment.surface_score,
                overall=overall,
                completeness=assessment.completeness,
            ),
            created_at=assessment.created_at,
            updated_at=assessment.updated_at,
        )


class GradePredictionOut(ApiModel):
    id: str
    card_id: str
    company_id: str | None
    company_code: str | None = None
    kind: str = PredictionKind.MARKET.value
    source: str
    model_version: str | None
    probabilities: dict[str, float]
    likely_grade: float | None
    grade_min: float | None
    grade_max: float | None
    max_grade_cap: float | None
    confidence: str = Confidence.LOW.value
    caps_applied: list[Any] | None
    explanation: list[Any] | None
    is_current: bool
    created_at: datetime

    @classmethod
    def from_model(cls, prediction: GradePrediction, company_code: str | None = None) -> GradePredictionOut:
        return cls(
            id=prediction.id,
            card_id=prediction.card_id,
            company_id=prediction.company_id,
            company_code=company_code,
            kind=prediction.kind,
            source=prediction.source,
            model_version=prediction.model_version,
            probabilities={str(k): float(v) for k, v in (prediction.probabilities or {}).items()},
            likely_grade=prediction.likely_grade,
            grade_min=prediction.grade_min,
            grade_max=prediction.grade_max,
            max_grade_cap=prediction.max_grade_cap,
            confidence=prediction.confidence,
            caps_applied=prediction.caps_applied,
            explanation=prediction.explanation,
            is_current=prediction.is_current,
            created_at=prediction.created_at,
        )


class GradePredictionOverride(ApiModel):
    """Spec section 35: the user can always overrule the model, and both numbers
    are kept."""

    company_id: str | None = None
    probabilities: dict[str, float] = Field(min_length=1)
    confidence: str = Confidence.MEDIUM.value
    notes: str | None = None

    @field_validator("probabilities")
    @classmethod
    def _normalised(cls, value: dict[str, float]) -> dict[str, float]:
        for grade, probability in value.items():
            try:
                float(grade)
            except ValueError as exc:
                raise ValueError(f"'{grade}' is not a grade") from exc
            if not 0 <= probability <= 1:
                raise ValueError("probabilities must be between 0 and 1")
        total = sum(value.values())
        if abs(total - 1.0) > 0.02:
            raise ValueError(f"probabilities must sum to 1 (got {total:.3f})")
        return value

    @field_validator("confidence")
    @classmethod
    def _valid_confidence(cls, value: str) -> str:
        if value not in Confidence.values():
            raise ValueError(f"confidence must be one of {Confidence.values()}")
        return value
