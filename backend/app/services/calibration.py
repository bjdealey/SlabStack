"""Marking the model against what actually happened, and correcting it.

Every other engine in this build reasons forward: condition to distribution,
distribution to expected value, expected value to a verdict. This one is the
only one that closes the loop — it takes grades that came back and asks whether
the model that predicted them was any good.

That makes it the feature that compounds. Everything else here could be rebuilt
from public data; a record of how *your* cards, assessed by *your* eye, come
back from *your* graders cannot be. It can only be earned.

Three things it must not do, all of them tempting:

**Do not score a prediction made after the fact.** The distribution being marked
is the one frozen onto the submission line when the card was sent. Recomputing
it now would mark a model against an outcome it has already seen, which measures
nothing and would flatter it enormously.

**Do not calibrate on a handful of results.** Four slabs cannot tell you your
eye runs half a grade high; they can tell you four cards came back. Below the
minimum sample the bias is measured and reported and explicitly *not* applied,
because a correction fitted to noise makes the model worse and does it
invisibly.

**Do not pool graders.** PSA's bias is not CGC's. A correction learned across
both describes neither, and the whole point is that a grader you send to often
becomes better understood over time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import BlockStatus, Confidence
from app.models import (
    Card,
    GradingCompany,
    GradingSubmission,
    PredictionResult,
    SubmissionCard,
)
from app.services import settings_service

__all__ = [
    "AccuracyReport",
    "Calibration",
    "CompanyAccuracy",
    "brier_score",
    "calibration_for",
    "correction_from_errors",
    "record_results_for_submission",
    "report",
]

#: Below this many scored results a company's bias is reported but never
#: applied. Configurable, because how much evidence is enough before you let a
#: correction touch your numbers is a judgement, not a constant.
DEFAULT_MINIMUM_SAMPLE = 10

#: The most a learned correction may move the centre, in grades. A measured
#: offset larger than this is far likelier to be a mis-set assessment habit or a
#: run of odd cards than a real two-grade bias, and letting it through would
#: wreck every prediction at once.
DEFAULT_MAX_OFFSET = 1.0


# --- Scoring one prediction --------------------------------------------------


def brier_score(probabilities: dict[str, float] | None, actual: float | None) -> float | None:
    """How wrong the whole distribution was. Lower is better; 0 is perfect.

    The multi-category Brier score: the squared distance between the probability
    vector and reality, where reality is 1 on the grade that happened and 0
    everywhere else. Marking the distribution rather than the mode is the point
    — being 95% sure of a 10 and being 40% sure of a 10 are very different
    claims, and only this notices when the confident one is wrong.

    ``None`` when there is nothing to mark, which is not the same as a bad score.
    """
    if not probabilities or actual is None:
        return None

    total = 0.0
    matched = False
    for key, value in probabilities.items():
        try:
            grade = float(key)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
        outcome = 1.0 if abs(grade - actual) < 1e-9 else 0.0
        if outcome:
            matched = True
        total += (float(value) - outcome) ** 2

    if not matched:
        # The actual grade was not in the distribution at all — the model gave
        # it zero probability. That is a real, and maximal, miss on that grade,
        # so it is added rather than ignored.
        total += 1.0
    return round(total, 4)


def _key(grade: float) -> str:
    return f"{grade:g}"


# --- Recording what came back ------------------------------------------------


def record_results_for_submission(db: Session, submission: GradingSubmission) -> int:
    """Write a ``prediction_results`` row for every graded card in a parcel.

    Idempotent: re-recording a submission updates the existing rows rather than
    stacking duplicates, so correcting a mistyped grade corrects the score too.

    A line with no frozen distribution produces no row. That happens when the
    card had no assessment when it was sent, and it is the honest outcome — there
    was no prediction, so there is nothing to mark.
    """
    if submission.company_id is None:
        return 0

    written = 0
    for line in submission.cards:
        if line.actual_grade is None:
            continue
        if not line.predicted_probabilities:
            continue

        existing = db.scalar(
            select(PredictionResult).where(
                PredictionResult.card_id == line.card_id,
                PredictionResult.submission_id == submission.id,
                PredictionResult.company_id == submission.company_id,
            )
        )
        row = existing or PredictionResult(
            card_id=line.card_id,
            submission_id=submission.id,
            company_id=submission.company_id,
        )
        row.actual_grade = line.actual_grade
        row.predicted_probabilities = dict(line.predicted_probabilities)
        row.predicted_likely_grade = line.predicted_grade
        row.brier_score = brier_score(line.predicted_probabilities, line.actual_grade)
        row.cert_number = line.cert_number
        row.graded_at = submission.returned_at
        if existing is None:
            db.add(row)
        written += 1

    return written


# --- The report --------------------------------------------------------------


@dataclass
class GradeBand:
    """One rung of the ladder: how often the model said it, how often it landed.

    This is the calibration curve. A well-calibrated model that says "40% a 10"
    across fifty cards should see roughly twenty 10s. Seeing five means the
    confidence is misplaced, and this is the row that shows it.
    """

    grade: float
    #: Summed predicted probability for this grade across every scored result.
    predicted_count: float = 0.0
    #: How many actually came back at it.
    actual_count: int = 0
    predicted_rate: float | None = None
    actual_rate: float | None = None
    #: Actual minus predicted, in percentage points. Negative = over-predicted.
    gap_pct: float | None = None


@dataclass
class ScoredResult:
    """One card, marked."""

    card_id: str
    name: str
    company_code: str | None
    predicted_grade: float | None
    actual_grade: float
    #: Actual minus predicted. Positive means it graded better than expected.
    surprise: float | None
    brier: float | None
    graded_at: str | None


@dataclass
class CompanyAccuracy:
    """How the model has done against one grader."""

    company_id: str
    company_code: str
    company_name: str
    scored: int = 0

    #: Landed exactly on the predicted grade.
    exact_pct: float | None = None
    #: Landed within half a grade, then within a full grade.
    within_half_pct: float | None = None
    within_one_pct: float | None = None

    #: Mean signed error, in grades. **This is the bias.** Positive means cards
    #: come back better than predicted, so the model reads harsh.
    mean_error: float | None = None
    mean_absolute_error: float | None = None
    #: Spread of the signed errors, which is what tells you whether the bias is
    #: a real shift or just a wide scatter around zero.
    error_stdev: float | None = None
    mean_brier: float | None = None

    bands: list[GradeBand] = field(default_factory=list)
    #: The one sentence worth reading, e.g. "your predicted CGC 10 rate runs 14
    #: points above your actual rate". ``None`` when nothing stands out.
    headline: str | None = None
    status: str = BlockStatus.OK.value
    reason: str | None = None


@dataclass
class AccuracyReport:
    scored: int = 0
    awaiting: int = 0
    companies: list[CompanyAccuracy] = field(default_factory=list)
    results: list[ScoredResult] = field(default_factory=list)
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE
    status: str = BlockStatus.OK.value
    reason: str | None = None


def report(db: Session, *, limit: int = 500) -> AccuracyReport:
    """Mark every recorded result, grouped by grader."""
    values = settings_service.get_all(db)
    minimum = int(values.get("calibration_minimum_sample", DEFAULT_MINIMUM_SAMPLE))
    result = AccuracyReport(minimum_sample=minimum)

    rows = list(
        db.scalars(
            select(PredictionResult).order_by(PredictionResult.created_at.desc()).limit(limit)
        )
    )
    # Cards sent and graded but never scoreable — no prediction was frozen, so
    # they are counted rather than quietly missing from the denominator.
    result.awaiting = (
        db.scalar(
            select(func.count())
            .select_from(SubmissionCard)
            .where(
                SubmissionCard.actual_grade.is_not(None),
                SubmissionCard.predicted_probabilities.is_(None),
            )
        )
        or 0
    )

    if not rows:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = (
            "No graded results recorded yet. Send a submission, record the grades when it comes "
            "back, and the model starts being marked against them."
        )
        return result

    companies = {row.id: row for row in db.scalars(select(GradingCompany))}
    by_company: dict[str, list[PredictionResult]] = {}
    for row in rows:
        by_company.setdefault(row.company_id, []).append(row)

    result.scored = len(rows)
    for company_id, group in by_company.items():
        company = companies.get(company_id)
        if company is None:  # pragma: no cover - FK guarantees this
            continue
        result.companies.append(_score_company(company, group, minimum))

    result.companies.sort(key=lambda item: item.scored, reverse=True)
    result.results = [
        ScoredResult(
            card_id=row.card_id,
            name=_card_name(db, row.card_id),
            company_code=companies[row.company_id].code if row.company_id in companies else None,
            predicted_grade=row.predicted_likely_grade,
            actual_grade=row.actual_grade,
            surprise=(
                round(row.actual_grade - row.predicted_likely_grade, 2)
                if row.predicted_likely_grade is not None
                else None
            ),
            brier=row.brier_score,
            graded_at=row.graded_at.isoformat() if row.graded_at else None,
        )
        for row in rows
    ]

    if result.awaiting:
        result.status = BlockStatus.PARTIAL.value
        result.reason = (
            f"{result.awaiting} graded card(s) had no prediction recorded when they were sent, "
            "so they cannot be marked. Cards added to a submission from now on will be."
        )
    return result


def _card_name(db: Session, card_id: str) -> str:
    card = db.get(Card, card_id)
    if card is None:  # pragma: no cover - FK guarantees this
        return card_id
    return f"{card.name} {card.card_number}".strip() if card.card_number else card.name


def _score_company(
    company: GradingCompany, rows: list[PredictionResult], minimum: int
) -> CompanyAccuracy:
    accuracy = CompanyAccuracy(
        company_id=company.id,
        company_code=company.code,
        company_name=company.name,
        scored=len(rows),
    )

    errors = [
        row.actual_grade - row.predicted_likely_grade
        for row in rows
        if row.predicted_likely_grade is not None
    ]
    briers = [row.brier_score for row in rows if row.brier_score is not None]

    if errors:
        accuracy.exact_pct = _pct(sum(1 for e in errors if abs(e) < 1e-9), len(errors))
        accuracy.within_half_pct = _pct(sum(1 for e in errors if abs(e) <= 0.5 + 1e-9), len(errors))
        accuracy.within_one_pct = _pct(sum(1 for e in errors if abs(e) <= 1.0 + 1e-9), len(errors))
        accuracy.mean_error = round(sum(errors) / len(errors), 3)
        accuracy.mean_absolute_error = round(sum(abs(e) for e in errors) / len(errors), 3)
        accuracy.error_stdev = _stdev(errors)
    if briers:
        accuracy.mean_brier = round(sum(briers) / len(briers), 4)

    accuracy.bands = _bands(rows)
    accuracy.headline = _headline(accuracy, company.code)

    if len(rows) < minimum:
        accuracy.status = BlockStatus.PARTIAL.value
        accuracy.reason = (
            f"Measured across {len(rows)} result(s). Below {minimum} this describes those cards "
            "rather than your eye, so it is reported but never applied to a prediction."
        )
    return accuracy


def _bands(rows: list[PredictionResult]) -> list[GradeBand]:
    """The calibration curve: predicted rate against observed rate, per grade."""
    grades: set[float] = set()
    for row in rows:
        grades.update(float(key) for key in (row.predicted_probabilities or {}))
        grades.add(row.actual_grade)

    bands: list[GradeBand] = []
    for grade in sorted(grades, reverse=True):
        band = GradeBand(grade=grade)
        for row in rows:
            band.predicted_count += float((row.predicted_probabilities or {}).get(_key(grade), 0.0))
            if abs(row.actual_grade - grade) < 1e-9:
                band.actual_count += 1
        band.predicted_rate = round(band.predicted_count / len(rows), 4)
        band.actual_rate = round(band.actual_count / len(rows), 4)
        band.gap_pct = round((band.actual_rate - band.predicted_rate) * 100, 1)
        band.predicted_count = round(band.predicted_count, 2)
        bands.append(band)
    return bands


def _headline(accuracy: CompanyAccuracy, company_code: str) -> str | None:
    """The single sentence worth reading, or nothing.

    Prefers the biggest per-grade miss over the overall bias, because "your
    predicted 10 rate runs 14 points above your actual rate" is actionable in a
    way that "mean error -0.13" is not.
    """
    if not accuracy.bands:
        return None

    worst = max(accuracy.bands, key=lambda band: abs(band.gap_pct or 0.0))
    gap = worst.gap_pct or 0.0
    if abs(gap) >= 5:
        direction = "above" if gap < 0 else "below"
        return (
            f"Your predicted {company_code} {worst.grade:g} rate runs {abs(gap):.0f} points "
            f"{direction} your actual rate."
        )

    if accuracy.mean_error is not None and abs(accuracy.mean_error) >= 0.1:
        reads = "harsh" if accuracy.mean_error > 0 else "generously"
        return (
            f"Cards come back {abs(accuracy.mean_error):.2f} grades "
            f"{'better' if accuracy.mean_error > 0 else 'worse'} than predicted on average — "
            f"your assessment reads {reads}."
        )
    return f"Predictions track {company_code}'s grading closely. Nothing to correct."


# --- The correction ----------------------------------------------------------


@dataclass
class Calibration:
    """A learned correction for one grader, and whether it is being applied."""

    company_id: str
    company_code: str
    sample_size: int = 0
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE
    #: Grades to add to the model's centre. Positive = the model reads harsh.
    grade_offset: float = 0.0
    #: Multiplier on the spread. Above 1.0 = the model was over-confident.
    spread_multiplier: float = 1.0
    #: False until there is enough evidence. The numbers above are still
    #: reported when false — measured, but not acted on.
    applied: bool = False
    confidence: str = Confidence.NONE.value
    reason: str | None = None

    @property
    def is_identity(self) -> bool:
        return abs(self.grade_offset) < 1e-9 and abs(self.spread_multiplier - 1.0) < 1e-9


def correction_from_errors(
    errors: list[float],
    *,
    company_code: str,
    company_id: str = "",
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
    max_offset: float = DEFAULT_MAX_OFFSET,
    enabled: bool = True,
) -> Calibration:
    """The correction itself, as pure arithmetic over the signed errors.

    Split out from ``calibration_for`` so it can be compared against the browser
    port over identical inputs without a database in the way — this is the part
    where the two implementations could silently drift.
    """
    result = Calibration(
        company_id=company_id, company_code=company_code, minimum_sample=minimum_sample
    )
    result.sample_size = len(errors)

    if not errors:
        result.reason = (
            f"No {company_code} results recorded yet, so there is nothing to learn from."
        )
        return result

    mean_error = sum(errors) / len(errors)
    stdev = _stdev(errors) or 0.0

    # Clamped, because a measured two-grade bias is far likelier to be a run of
    # odd cards than a real one, and applying it would wreck every prediction.
    result.grade_offset = round(max(-max_offset, min(max_offset, mean_error)), 3)

    # If the model's own spread was narrower than the errors it actually made,
    # it was over-confident and the range needs widening. Never narrowed below
    # the model's own spread: claiming *more* precision than the rules engine
    # does on the strength of a few dozen cards is exactly backwards.
    result.spread_multiplier = round(max(1.0, stdev / _NOMINAL_SIGMA), 3) if stdev else 1.0

    if not enabled:
        result.reason = (
            "Calibration is switched off in Settings, so the measurement is reported but not "
            "applied."
        )
        return result

    if len(errors) < minimum_sample:
        result.reason = (
            f"{len(errors)} of the {minimum_sample} results needed before a correction is "
            "applied. A bias fitted to this few cards is noise, and correcting for noise makes "
            "the model worse without saying so."
        )
        return result

    result.applied = True
    result.confidence = (
        Confidence.HIGH.value
        if len(errors) >= minimum_sample * 3
        else Confidence.MEDIUM.value
        if len(errors) >= minimum_sample * 2
        else Confidence.LOW.value
    )
    if result.is_identity:
        result.reason = (
            f"{len(errors)} {company_code} result(s) and nothing to correct — predictions already "
            "track this grader."
        )
    else:
        moves = "up" if result.grade_offset > 0 else "down"
        result.reason = (
            f"Learned from {len(errors)} {company_code} result(s): the centre moves {moves} by "
            f"{abs(result.grade_offset):.2f} grades"
            + (
                f" and the range widens by {(result.spread_multiplier - 1) * 100:.0f}%."
                if result.spread_multiplier > 1.0
                else "."
            )
        )
    return result


def calibration_for(db: Session, company: GradingCompany) -> Calibration:
    """What this grader's history says the model should do differently.

    The correction lands on the two parameters the model already has — where the
    distribution is centred and how wide it is — rather than inventing a new
    mechanism. That keeps it inspectable: a calibrated prediction is the same
    model with a shifted centre, and the shift is a number you can read.
    """
    values = settings_service.get_all(db)
    rows = list(
        db.scalars(select(PredictionResult).where(PredictionResult.company_id == company.id))
    )
    errors = [
        row.actual_grade - row.predicted_likely_grade
        for row in rows
        if row.predicted_likely_grade is not None
    ]
    return correction_from_errors(
        errors,
        company_code=company.code,
        company_id=company.id,
        minimum_sample=int(values.get("calibration_minimum_sample", DEFAULT_MINIMUM_SAMPLE)),
        max_offset=float(values.get("calibration_max_offset", DEFAULT_MAX_OFFSET)),
        enabled=bool(values.get("calibration_enabled", True)),
    )


#: The spread the rules engine typically produces for a complete assessment.
#: Used as the denominator for the learned spread correction, so a multiplier of
#: 1.0 means "the model's own confidence was about right".
_NOMINAL_SIGMA = 0.55


def _pct(count: int, total: int) -> float | None:
    return round(count / total * 100, 1) if total else None


def _stdev(values: list[float]) -> float | None:
    """Sample standard deviation. ``None`` below two points, which have none."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round(math.sqrt(variance), 3)
