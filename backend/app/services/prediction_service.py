"""The grade probability model (spec sections 8, 9).

Turns a condition assessment into a probability distribution over grades. It is
a rules engine plus a spread model, not machine learning, and every number it
uses is either configuration or documented here as our own estimate.

**How it works**

1. *Base grade* — a blend of the weighted mean of the condition sub-scores and
   the *worst* of them. The blend matters: grading is closer to "your weakest
   attribute bounds you" than to an average, so a card that is perfect on three
   counts and poor on centering is not a 9.4. Sub-scores that were never
   assessed are excluded and the remaining weights renormalised, so an
   unanswered field lowers confidence rather than silently counting as ten.

2. *Spread* — graders are not deterministic. The same card submitted twice can
   come back a 9 and a 10, so the model never returns a point estimate. Sigma
   grows with three things: how much of the assessment is missing, how much the
   sub-scores disagree with each other (a 10/10/10/6 card is less predictable
   than a 9/9/9/9 card), and an irreducible floor for grader inconsistency.

3. *Company adjustment* — ``grading_companies.strictness`` shifts the centre for
   graders the user finds harsher or softer. It ships at 0.0 for everyone: we
   make no claim about who grades harder, and the user tunes it from their own
   results (Phase 8 can then calibrate it automatically).

4. *Discretisation* — the continuous estimate is integrated over each grade's
   bucket on that company's ladder (whole grades, or halves where the company
   awards them). Mass beyond either end folds into the end grade.

5. *Caps* — a rule like "major crease" sets a ceiling. The centre is pulled down
   to the cap and any mass above it is removed, because a creased card is not
   "a 9 that got truncated", it is a low-grade card.

6. *Multipliers* — softer rules scale the mass at or above a grade without
   moving the whole distribution: minor whitening makes a 10 less likely without
   making the card a 6.

**What this is not.** These are not PSA's, CGC's or ACE's published standards,
and no grader endorses them. They are a starting model, held in ``grade_rules``
and settings precisely so the user can disagree with them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.enums import CORNER_FIELDS, Confidence, PredictionKind, Severity
from app.models import ConditionAssessment, GradeRule, GradingCompany

MODEL_VERSION = "rules-1.0"

# Severity ordering. "unknown" never triggers a rule: not looked at is not the
# same as not present.
SEVERITY_ORDER: dict[str, int] = {
    Severity.NONE.value: 0,
    Severity.MINOR.value: 1,
    Severity.MODERATE.value: 2,
    Severity.SEVERE.value: 3,
}

# Pseudo-fields a rule may target, expanded to real assessment fields. Lets one
# rule cover "any corner" without four near-identical rows.
FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "corner_any": CORNER_FIELDS,
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "centering": 0.25,
    "corners": 0.25,
    "edges": 0.20,
    "surface": 0.30,
}

SUBSCORE_ATTRS: dict[str, str] = {
    "centering": "centering_score",
    "corners": "corners_score",
    "edges": "edges_score",
    "surface": "surface_score",
}


@dataclass
class ModelParameters:
    """Tunable model constants, resolved from settings."""

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    worst_weight: float = 0.45
    base_sigma: float = 0.45
    unknown_sigma: float = 1.6
    disagreement_factor: float = 0.25
    max_sigma: float = 3.0
    min_probability: float = 0.005

    @classmethod
    def from_settings(cls, values: dict[str, Any]) -> ModelParameters:
        weights = values.get("grade_model_weights") or DEFAULT_WEIGHTS
        return cls(
            weights={key: float(weights.get(key, DEFAULT_WEIGHTS[key])) for key in DEFAULT_WEIGHTS},
            worst_weight=float(values.get("grade_model_worst_weight", 0.45)),
            base_sigma=float(values.get("grade_model_base_sigma", 0.45)),
            unknown_sigma=float(values.get("grade_model_unknown_sigma", 1.6)),
            disagreement_factor=float(values.get("grade_model_disagreement_factor", 0.25)),
            max_sigma=float(values.get("grade_model_max_sigma", 3.0)),
            min_probability=float(values.get("grade_model_min_probability", 0.005)),
        )


@dataclass
class AppliedRule:
    code: str
    label: str
    field: str
    face: str
    severity: str
    max_grade: float | None = None
    probability_multiplier: float | None = None
    penalty_from_grade: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "field": self.field,
            "face": self.face,
            "severity": self.severity,
            "max_grade": self.max_grade,
            "probability_multiplier": self.probability_multiplier,
            "penalty_from_grade": self.penalty_from_grade,
        }


@dataclass
class Prediction:
    probabilities: dict[str, float]
    likely_grade: float
    grade_min: float
    grade_max: float
    confidence: str
    max_grade_cap: float | None
    caps_applied: list[dict[str, Any]]
    explanation: list[dict[str, Any]]
    base_grade: float
    sigma: float
    model_version: str = MODEL_VERSION


class NotEnoughAssessmentError(ValueError):
    """Raised when an assessment has no answered fields to predict from."""


# ---------------------------------------------------------------------------
# Sub-scores -> a central estimate and a spread
# ---------------------------------------------------------------------------


def _base_grade(assessment: ConditionAssessment, params: ModelParameters) -> tuple[float, float]:
    """Central estimate from the assessed sub-scores, and how much they disagree.

    The estimate blends the weighted mean with the worst sub-score. A pure mean
    lets three perfect attributes hide one bad one — a card with 62/38 centering
    and flawless everything else would average to 9.4 and be called a likely 10,
    which is not how it would come back. Weighting the worst attribute pulls that
    down without ignoring the rest.

    Returns ``(base, disagreement)``. Raises if nothing has been assessed:
    predicting from an empty assessment would be inventing a number.
    """
    pairs: list[tuple[float, float]] = []
    for key, attr in SUBSCORE_ATTRS.items():
        value = getattr(assessment, attr, None)
        if value is not None:
            pairs.append((float(value), params.weights.get(key, 0.0)))

    if not pairs:
        raise NotEnoughAssessmentError(
            "This assessment has no answered fields, so there is nothing to predict from."
        )

    scores = [score for score, _ in pairs]
    total_weight = sum(weight for _, weight in pairs)
    if total_weight <= 0:
        mean = sum(scores) / len(scores)
    else:
        mean = sum(score * weight for score, weight in pairs) / total_weight

    worst = min(scores)
    blend = min(max(params.worst_weight, 0.0), 1.0)
    base = (1.0 - blend) * mean + blend * worst

    if len(scores) > 1:
        average = sum(scores) / len(scores)
        disagreement = math.sqrt(sum((score - average) ** 2 for score in scores) / len(scores))
    else:
        disagreement = 0.0

    return base, disagreement


def _sigma(completeness: float, disagreement: float, params: ModelParameters) -> float:
    unknown = max(0.0, 1.0 - completeness)
    sigma = (
        params.base_sigma
        + unknown * params.unknown_sigma
        + disagreement * params.disagreement_factor
    )
    return min(sigma, params.max_sigma)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def load_rules(db: Session, company_id: str | None) -> list[GradeRule]:
    """Active rules: the shared ones, plus any specific to this company."""
    stmt = select(GradeRule).where(GradeRule.active.is_(True))
    if company_id is None:
        stmt = stmt.where(GradeRule.company_id.is_(None))
    else:
        stmt = stmt.where(or_(GradeRule.company_id.is_(None), GradeRule.company_id == company_id))
    return list(db.scalars(stmt.order_by(GradeRule.sort_order, GradeRule.code)))


def _observed_severity(assessment: ConditionAssessment, rule: GradeRule) -> tuple[str, str] | None:
    """The worst severity this rule sees, and which face it was on."""
    fields = FIELD_GROUPS.get(rule.field, (rule.field,))
    faces = ("front", "back") if (rule.face or "any") == "any" else (rule.face,)

    worst: tuple[str, str] | None = None
    for face in faces:
        for name in fields:
            value = getattr(assessment, f"{face}_{name}", None)
            if value not in SEVERITY_ORDER:
                continue
            if worst is None or SEVERITY_ORDER[value] > SEVERITY_ORDER[worst[0]]:
                worst = (value, face)
    return worst


def evaluate_rules(
    assessment: ConditionAssessment, rules: list[GradeRule]
) -> tuple[list[AppliedRule], list[AppliedRule]]:
    """Split the triggered rules into hard caps and probability multipliers."""
    caps: list[AppliedRule] = []
    multipliers: list[AppliedRule] = []

    for rule in rules:
        observed = _observed_severity(assessment, rule)
        if observed is None:
            continue
        severity, face = observed
        threshold = SEVERITY_ORDER.get(rule.min_severity, SEVERITY_ORDER[Severity.MINOR.value])
        if SEVERITY_ORDER[severity] < threshold or SEVERITY_ORDER[severity] == 0:
            continue

        applied = AppliedRule(
            code=rule.code,
            label=rule.label,
            field=rule.field,
            face=face,
            severity=severity,
            max_grade=rule.max_grade,
            probability_multiplier=rule.probability_multiplier,
            penalty_from_grade=rule.penalty_from_grade,
        )
        if rule.max_grade is not None:
            caps.append(applied)
        if rule.probability_multiplier is not None:
            multipliers.append(applied)

    return caps, multipliers


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------


def grade_ladder(company: GradingCompany | None) -> list[float]:
    """The grades a company actually awards, highest first."""
    top = company.grade_scale_max if company else 10.0
    halves = bool(company and company.supports_half_grades)
    step = 0.5 if halves else 1.0

    grades: list[float] = []
    value = 1.0
    while value <= top + 1e-9:
        grades.append(round(value, 1))
        value += step
    return sorted(grades, reverse=True)


def _normal_cdf(x: float, mean: float, sigma: float) -> float:
    if sigma <= 0:
        return 0.0 if x < mean else 1.0
    return 0.5 * (1.0 + math.erf((x - mean) / (sigma * math.sqrt(2.0))))


def _discretise(centre: float, sigma: float, ladder: list[float]) -> dict[float, float]:
    """Integrate the estimate over each grade's bucket.

    The two ends are handled differently, and deliberately so.

    The *top* grade absorbs everything above it: a card estimated at 11 is a
    top-grade card, not an impossible one.

    The *bottom* grade does not absorb everything below it. It is tempting to
    mirror the top, but a card capped at 3 with a wide spread would then have
    grade 1 swallow the whole lower tail and come out as the single most likely
    outcome — the model would announce "probably a 1" about a card it actually
    believes is a 3. Mass below the scale is discarded and the rest renormalised
    instead.
    """
    ascending = sorted(ladder)
    step = (ascending[1] - ascending[0]) if len(ascending) > 1 else 1.0
    half = step / 2.0

    weights: dict[float, float] = {}
    for index, grade in enumerate(ascending):
        low = grade - half
        high = math.inf if index == len(ascending) - 1 else grade + half
        lower = _normal_cdf(low, centre, sigma)
        upper = 1.0 if high == math.inf else _normal_cdf(high, centre, sigma)
        weights[grade] = max(0.0, upper - lower)
    return weights


def _normalise(weights: dict[float, float]) -> dict[float, float]:
    total = sum(weights.values())
    if total <= 0:
        # Degenerate input; spread evenly rather than returning nothing.
        return {grade: 1.0 / len(weights) for grade in weights}
    return {grade: value / total for grade, value in weights.items()}


def _credible_range(probabilities: dict[float, float], mass: float = 0.8) -> tuple[float, float]:
    """The narrowest run of adjacent grades holding at least ``mass``."""
    grades = sorted(probabilities, reverse=True)
    best: tuple[float, float] | None = None
    best_width = math.inf

    for start in range(len(grades)):
        total = 0.0
        for end in range(start, len(grades)):
            total += probabilities[grades[end]]
            if total >= mass - 1e-9:
                width = grades[start] - grades[end]
                if width < best_width:
                    best_width = width
                    best = (grades[end], grades[start])
                break

    if best is None:
        return min(grades), max(grades)
    return best


def _confidence(completeness: float, sigma: float, params: ModelParameters) -> str:
    """How much to trust the distribution — driven mostly by how much was assessed."""
    if completeness >= 0.9 and sigma <= params.base_sigma + 0.35:
        return Confidence.HIGH.value
    if completeness >= 0.6:
        return Confidence.MEDIUM.value
    if completeness >= 0.25:
        return Confidence.LOW.value
    return Confidence.NONE.value


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def predict(
    assessment: ConditionAssessment,
    *,
    company: GradingCompany | None,
    rules: list[GradeRule],
    params: ModelParameters,
    kind: str = PredictionKind.MARKET.value,
    calibration_offset: float = 0.0,
    spread_multiplier: float = 1.0,
) -> Prediction:
    """Produce a grade distribution for one card against one company.

    ``kind`` selects the question being answered. ``physical`` describes the
    card itself and ignores who is grading it; ``market`` adds the company's
    strictness and its own rules. Spec section 8 keeps these apart because they
    fail differently: a physical estimate can be right while a market estimate
    is wrong about the grader.

    ``calibration_offset`` and ``spread_multiplier`` are what the user's own
    returned grades have taught the model about this grader — plain numbers
    rather than an object, so this function stays pure and the learning service
    can import it without a cycle. They default to "learned nothing", which is
    the correct behaviour for a fresh install and for a physical prediction:
    calibration is measured against a grader, and the physical question has none.
    """
    is_market = kind == PredictionKind.MARKET.value

    base, disagreement = _base_grade(assessment, params)
    completeness = float(assessment.completeness or 0.0)
    sigma = _sigma(completeness, disagreement, params)

    if is_market and spread_multiplier > 1.0:
        sigma *= spread_multiplier

    centre = base
    explanation: list[dict[str, Any]] = [
        {
            "kind": "info",
            "text": f"Condition sub-scores average {base:.1f}/10.",
            "detail": _subscore_detail(assessment),
        }
    ]

    strictness = float(getattr(company, "strictness", 0.0) or 0.0) if company else 0.0
    if is_market and strictness:
        centre += strictness
        direction = "harder" if strictness < 0 else "more generously"
        explanation.append(
            {
                "kind": "info",
                "text": f"{company.code} is set to grade {direction} by {abs(strictness):.2f}.",
                "detail": "Your setting, not a published standard — tune it in Settings → Grading.",
            }
        )

    # Kept separate from `strictness` on purpose. That is a number the user set
    # about the grader; this is a number measured from their own results. Adding
    # a learned correction into the user's setting would overwrite an opinion
    # with an observation and lose the ability to tell them apart.
    if is_market and calibration_offset:
        centre += calibration_offset
        moves = "up" if calibration_offset > 0 else "down"
        explanation.append(
            {
                "kind": "info",
                "text": (
                    f"Adjusted {moves} {abs(calibration_offset):.2f} grades from your own "
                    f"{company.code} results."
                ),
                "detail": (
                    "Learned from how your cards have actually come back, not from a published "
                    "standard. Turn it off in Settings → Grade model."
                ),
            }
        )
    if is_market and spread_multiplier > 1.0:
        explanation.append(
            {
                "kind": "warn",
                "text": f"Range widened {(spread_multiplier - 1) * 100:.0f}% from your results.",
                "detail": (
                    "Your grades have scattered more widely than the model expected, so it is "
                    "less sure than it would otherwise claim to be."
                ),
            }
        )

    caps, multipliers = evaluate_rules(assessment, rules if is_market else _generic(rules))

    cap_value: float | None = None
    if caps:
        cap_value = min(rule.max_grade for rule in caps if rule.max_grade is not None)
        centre = min(centre, cap_value)
        for rule in caps:
            explanation.append(
                {
                    "kind": "fail",
                    "text": f"{rule.label} caps this at {rule.max_grade:g}.",
                    "detail": f"{rule.face.capitalize()} {rule.field.replace('_', ' ')}: {rule.severity}.",
                }
            )

    ladder = grade_ladder(company if is_market else None)
    weights = _discretise(centre, sigma, ladder)

    if cap_value is not None:
        for grade in list(weights):
            if grade > cap_value + 1e-9:
                weights[grade] = 0.0

    for rule in multipliers:
        threshold = rule.penalty_from_grade
        multiplier = rule.probability_multiplier
        if threshold is None or multiplier is None:
            continue
        for grade in list(weights):
            if grade >= threshold - 1e-9:
                weights[grade] *= multiplier
        explanation.append(
            {
                "kind": "warn",
                "text": f"{rule.label} makes {threshold:g}+ less likely.",
                "detail": f"Probability at {threshold:g} and above scaled to {multiplier:.0%}.",
            }
        )

    probabilities = _normalise(weights)

    # Drop negligible tails so the output reads as a decision aid, not a census.
    trimmed = {
        grade: value for grade, value in probabilities.items() if value >= params.min_probability
    }
    probabilities = _normalise(trimmed or probabilities)

    likely = max(probabilities, key=lambda grade: probabilities[grade])
    low, high = _credible_range(probabilities)
    confidence = _confidence(completeness, sigma, params)

    if completeness < 0.6:
        explanation.append(
            {
                "kind": "warn",
                "text": f"Only {completeness:.0%} of the assessment is filled in.",
                "detail": "The range stays wide until the rest is answered.",
            }
        )

    return Prediction(
        probabilities={_grade_key(grade): round(value, 4) for grade, value in probabilities.items()},
        likely_grade=likely,
        grade_min=low,
        grade_max=high,
        confidence=confidence,
        max_grade_cap=cap_value,
        caps_applied=[rule.as_dict() for rule in caps],
        explanation=explanation,
        base_grade=round(base, 2),
        sigma=round(sigma, 3),
    )


def _generic(rules: list[GradeRule]) -> list[GradeRule]:
    """Company-agnostic rules only, for a physical-condition prediction."""
    return [rule for rule in rules if rule.company_id is None]


def _grade_key(grade: float) -> str:
    return f"{grade:g}"


def _subscore_detail(assessment: ConditionAssessment) -> str:
    parts = []
    for label, attr in SUBSCORE_ATTRS.items():
        value = getattr(assessment, attr, None)
        parts.append(f"{label} {value:.1f}" if value is not None else f"{label} —")
    return ", ".join(parts)
