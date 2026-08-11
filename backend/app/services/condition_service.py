"""Condition sub-scores.

This module does arithmetic, not grading. It converts the structured assessment
into four 0-10 sub-scores (centering, corners, edges, surface) plus a
completeness fraction. It deliberately does **not** produce grade probabilities:
that is the Phase 2 rules engine, which reads these scores together with the
configurable caps in ``grade_rules``.

The centering anchors below are our tolerance model, not a published standard.
They are the numbers a user would tune first, which is why they sit in one
readable table.
"""

from __future__ import annotations

from itertools import pairwise

from app.enums import CORNER_FIELDS, DEFECT_FIELDS, Severity
from app.models import ConditionAssessment

# Worst-side percentage -> score. Front is judged harder than back, matching how
# graders actually behave (a 60/40 back is unremarkable; a 60/40 front is not).
FRONT_CENTERING_ANCHORS: tuple[tuple[float, float], ...] = (
    (50.0, 10.0),
    (52.5, 10.0),
    (55.0, 9.0),
    (60.0, 8.0),
    (65.0, 7.0),
    (70.0, 6.0),
    (75.0, 5.0),
    (85.0, 3.0),
    (100.0, 0.0),
)

BACK_CENTERING_ANCHORS: tuple[tuple[float, float], ...] = (
    (50.0, 10.0),
    (60.0, 10.0),
    (70.0, 9.0),
    (75.0, 8.5),
    (80.0, 7.0),
    (90.0, 4.0),
    (100.0, 0.0),
)

SEVERITY_PENALTY: dict[str, float] = {
    Severity.NONE.value: 0.0,
    Severity.MINOR.value: 1.5,
    Severity.MODERATE.value: 3.5,
    Severity.SEVERE.value: 6.0,
}

CORNER_GROUP: tuple[str, ...] = CORNER_FIELDS
EDGE_GROUP: tuple[str, ...] = ("edge_condition", "whitening", "silvering")
SURFACE_GROUP: tuple[str, ...] = (
    "surface_condition",
    "holo_condition",
    "scratches",
    "print_lines",
    "dents",
    "dimpling",
    "creases",
    "staining",
    "misc_defects",
)


def _interpolate(anchors: tuple[tuple[float, float], ...], value: float) -> float:
    if value <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in pairwise(anchors):
        if value <= x1:
            if x1 == x0:
                return y1
            ratio = (value - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return anchors[-1][1]


def centering_face_score(
    left: float | None,
    right: float | None,
    top: float | None,
    bottom: float | None,
    *,
    is_front: bool,
) -> float | None:
    """Score one face from its border percentages.

    Both axes are normalised to "worst side as a percentage of that axis", so
    48/52 and 52/48 score identically, and a user who enters 4.8mm/5.2mm instead
    of percentages still gets the right answer.
    """
    anchors = FRONT_CENTERING_ANCHORS if is_front else BACK_CENTERING_ANCHORS
    ratios: list[float] = []
    for a, b in ((left, right), (top, bottom)):
        if a is None or b is None:
            continue
        total = a + b
        if total <= 0:
            continue
        ratios.append(max(a, b) / total * 100.0)
    if not ratios:
        return None
    # The worse axis decides the face: good left/right does not rescue bad
    # top/bottom.
    return round(_interpolate(anchors, max(ratios)), 2)


def _group_score(assessment: ConditionAssessment, fields: tuple[str, ...]) -> float | None:
    penalties: list[float] = []
    for face in ("front", "back"):
        for field in fields:
            value = getattr(assessment, f"{face}_{field}", None)
            if value in SEVERITY_PENALTY:
                penalties.append(SEVERITY_PENALTY[value])
    if not penalties:
        return None
    penalties.sort(reverse=True)
    # The worst defect dominates; the rest accumulate at a discount, because ten
    # separate minor nicks are worse than one but not ten times worse.
    score = 10.0 - penalties[0] - 0.4 * sum(penalties[1:])
    return round(max(0.0, min(10.0, score)), 2)


def completeness(assessment: ConditionAssessment) -> float:
    total = len(DEFECT_FIELDS) * 2 + 8
    answered = 0
    for face in ("front", "back"):
        for field in DEFECT_FIELDS:
            # A field counts as answered only when it holds a real severity.
            # "unknown" and an unset column both mean "not looked at yet".
            if getattr(assessment, f"{face}_{field}", None) in SEVERITY_PENALTY:
                answered += 1
    for face in ("front", "back"):
        for edge in ("left", "right", "top", "bottom"):
            if getattr(assessment, f"{face}_centering_{edge}", None) is not None:
                answered += 1
    return round(answered / total, 4)


def recompute_scores(assessment: ConditionAssessment) -> ConditionAssessment:
    """Recompute every derived column on the assessment, in place."""
    assessment.centering_score_front = centering_face_score(
        assessment.front_centering_left,
        assessment.front_centering_right,
        assessment.front_centering_top,
        assessment.front_centering_bottom,
        is_front=True,
    )
    assessment.centering_score_back = centering_face_score(
        assessment.back_centering_left,
        assessment.back_centering_right,
        assessment.back_centering_top,
        assessment.back_centering_bottom,
        is_front=False,
    )

    faces = [s for s in (assessment.centering_score_front, assessment.centering_score_back) if s is not None]
    # Each face is already scored against its own tolerance, so the combined
    # score is simply the weaker of the two.
    assessment.centering_score = round(min(faces), 2) if faces else None

    assessment.corners_score = _group_score(assessment, CORNER_GROUP)
    assessment.edges_score = _group_score(assessment, EDGE_GROUP)
    assessment.surface_score = _group_score(assessment, SURFACE_GROUP)
    assessment.completeness = completeness(assessment)
    return assessment


def overall_condition_score(assessment: ConditionAssessment) -> float | None:
    """A single 0-10 headline number, weighted the way graders weight faults.

    Presentational only. The decision engine uses the sub-scores and the
    configurable caps, never this.
    """
    weights = {
        "centering_score": 0.25,
        "corners_score": 0.25,
        "edges_score": 0.20,
        "surface_score": 0.30,
    }
    total_weight = 0.0
    total = 0.0
    for attr, weight in weights.items():
        value = getattr(assessment, attr, None)
        if value is None:
            continue
        total += value * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return round(total / total_weight, 2)
