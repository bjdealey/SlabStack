"""Submissions: build a parcel, cost it honestly, and follow it out and back.

The costing lives in ``app.services.submissions`` and the packing in
``app.services.optimiser``; this module is the seam between them and HTTP.

Money crosses this boundary in major units, as it does everywhere else in the
API — the engines work in integer minor units and convert once, here.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import ConflictError, NotFoundError
from app.enums import CostAllocationMethod, SubmissionCardStatus, SubmissionStatus
from app.models import Card, GradingCompany, GradingSubmission, GradingTier, SubmissionCard
from app.money import to_major, to_minor
from app.schemas.common import ApiModel
from app.services import calibration, optimiser, submissions

router = APIRouter(prefix="/submissions", tags=["submissions"])


# --- Payloads ----------------------------------------------------------------


class SubmissionWrite(ApiModel):
    name: str | None = None
    company_id: str | None = None
    tier_id: str | None = None
    status: str | None = None
    cost_allocation_method: str | None = None
    shipping_out: float | None = None
    shipping_return: float | None = None
    handling: float | None = None
    other_fees: float | None = None
    submitted_at: str | None = None
    received_at: str | None = None
    returned_at: str | None = None
    tracking_outbound: str | None = None
    tracking_return: str | None = None
    notes: str | None = None


class SubmissionCreate(SubmissionWrite):
    company_id: str
    card_ids: list[str] = Field(default_factory=list)


class CardAdd(ApiModel):
    card_ids: list[str] = Field(min_length=1)
    tier_id: str | None = None


class CardUpdate(ApiModel):
    tier_id: str | None = None
    declared_value: float | None = None
    actual_grade: float | None = None
    cert_number: str | None = None
    status: str | None = None
    sort_order: int | None = None
    notes: str | None = None


# --- Responses ---------------------------------------------------------------


class CardLineOut(ApiModel):
    """One card's line, with its share of everything the parcel shares."""

    submission_card_id: str
    card_id: str
    name: str
    set_label: str | None = None
    tier_id: str | None = None
    tier_name: str | None = None
    declared_value: float | None = None
    declared_value_source: str = "system"
    declared_value_confidence: str | None = None
    base_fee: float | None = None
    membership_discount: float | None = None
    grading_fee: float | None = None
    per_card_fees: float | None = None
    declared_value_fee: float | None = None
    allocated_overhead: float | None = None
    total_cost: float | None = None
    allocation_weight: int = 1
    predicted_grade: float | None = None
    actual_grade: float | None = None
    status: str = "planned"
    sort_order: int = 0
    blockers: list[str] = Field(default_factory=list)


class TierGroupOut(ApiModel):
    tier_id: str | None = None
    tier_name: str | None = None
    company_code: str
    card_count: int = 0
    minimum_cards: int = 1
    maximum_cards: int | None = None
    short_by: int = 0
    over_by: int = 0
    blockers: list[str] = Field(default_factory=list)


class SubmissionOut(ApiModel):
    id: str
    reference: str
    name: str | None = None
    status: str
    currency: str = "GBP"
    company_id: str | None = None
    company_code: str | None = None
    company_name: str | None = None
    tier_id: str | None = None

    card_count: int = 0
    declared_value_total: float | None = None

    shipping_out: float | None = None
    shipping_return: float | None = None
    insurance: float | None = None
    handling: float | None = None
    other_fees: float | None = None
    tier_additional_fees: float | None = None
    shared_pot: float | None = None

    grading_fees: float | None = None
    per_card_fees: float | None = None
    declared_value_fees: float | None = None
    membership_discount: float | None = None
    total_cost: float | None = None
    cost_per_card: float | None = Field(
        default=None, description="An average, and null with no cards — not zero."
    )

    allocation_method: str = CostAllocationMethod.EQUAL.value
    allocation_note: str | None = None
    membership_code: str | None = None

    submitted_at: str | None = None
    received_at: str | None = None
    returned_at: str | None = None
    tracking_outbound: str | None = None
    tracking_return: str | None = None
    notes: str | None = None

    tiers: list[TierGroupOut] = Field(default_factory=list)
    cards: list[CardLineOut] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlacedCardOut(ApiModel):
    card_id: str
    name: str
    set_label: str | None = None
    company_code: str | None = None
    tier_id: str | None = None
    tier_name: str | None = None
    declared_value: float | None = None
    decision_when_routed: str
    decision_in_batch: str
    expected_profit: float | None = None
    grading_cost: float | None = None
    opportunity_score: float | None = None
    still_pays: bool = True
    reason: str | None = None
    cheaper_tier_name: str | None = None
    cheaper_tier_saving: float | None = None


class ProposedBatchOut(ApiModel):
    company_id: str
    company_code: str
    tier_id: str | None = None
    tier_name: str | None = None
    effective_tier_name: str | None = Field(
        default=None,
        description="Where these cards actually land at the current count, which differs "
        "from tier_name while the batch is short of its minimum.",
    )
    card_count: int = 0
    minimum_cards: int = 1
    maximum_cards: int | None = None
    short_by: int = 0
    expected_profit: float | None = None
    grading_cost: float | None = None
    expected_profit_if_filled: float | None = None
    viable: bool = True
    reason: str | None = None
    cards: list[PlacedCardOut] = Field(default_factory=list)


class UnplacedCardOut(ApiModel):
    card_id: str
    name: str
    set_label: str | None = None
    company_code: str | None = None
    tier_name: str | None = None
    expected_profit: float | None = None
    reason: str = ""


class OptimiserOut(ApiModel):
    status: str
    reason: str | None = None
    currency: str = "GBP"
    analysable: int = 0
    worth_grading: int = 0
    placed: int = 0
    total_cards: int = 0
    truncated: bool = False
    routed_at_batch_size: int = 1
    expected_profit: float | None = None
    total_grading_cost: float | None = None
    batches: list[ProposedBatchOut] = Field(default_factory=list)
    unplaced: list[UnplacedCardOut] = Field(default_factory=list)
    stopped_paying: list[PlacedCardOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --- Mapping -----------------------------------------------------------------


def _out(db, submission: GradingSubmission) -> SubmissionOut:
    costing = submissions.cost_submission(db, submission)
    return SubmissionOut(
        id=costing.submission_id,
        reference=costing.reference,
        name=costing.name,
        status=costing.status,
        currency=costing.currency,
        company_id=costing.company_id,
        company_code=costing.company_code,
        company_name=costing.company_name,
        tier_id=submission.tier_id,
        card_count=costing.card_count,
        declared_value_total=to_major(costing.declared_value_total_minor),
        shipping_out=to_major(costing.shipping_out_minor),
        shipping_return=to_major(costing.shipping_return_minor),
        insurance=to_major(costing.insurance_minor),
        handling=to_major(costing.handling_minor),
        other_fees=to_major(costing.other_fees_minor),
        tier_additional_fees=to_major(costing.tier_additional_fees_minor),
        shared_pot=to_major(costing.shared_pot_minor),
        grading_fees=to_major(costing.grading_fees_minor),
        per_card_fees=to_major(costing.per_card_fees_minor),
        declared_value_fees=to_major(costing.declared_value_fees_minor),
        membership_discount=to_major(costing.membership_discount_minor),
        total_cost=to_major(costing.total_minor),
        cost_per_card=to_major(costing.cost_per_card_minor),
        allocation_method=costing.allocation_method,
        allocation_note=costing.allocation_note,
        membership_code=costing.membership_code,
        submitted_at=submission.submitted_at.isoformat() if submission.submitted_at else None,
        received_at=submission.received_at.isoformat() if submission.received_at else None,
        returned_at=submission.returned_at.isoformat() if submission.returned_at else None,
        tracking_outbound=submission.tracking_outbound,
        tracking_return=submission.tracking_return,
        notes=submission.notes,
        tiers=[
            TierGroupOut(
                tier_id=group.tier_id,
                tier_name=group.tier_name,
                company_code=group.company_code,
                card_count=group.card_count,
                minimum_cards=group.minimum_cards,
                maximum_cards=group.maximum_cards,
                short_by=group.short_by,
                over_by=group.over_by,
                blockers=group.blockers,
            )
            for group in costing.tiers
        ],
        cards=[
            CardLineOut(
                submission_card_id=line.submission_card_id,
                card_id=line.card_id,
                name=line.name,
                set_label=line.set_label,
                tier_id=line.tier_id,
                tier_name=line.tier_name,
                declared_value=to_major(line.declared_value_minor),
                declared_value_source=line.declared_value_source,
                declared_value_confidence=line.declared_value_confidence,
                base_fee=to_major(line.base_fee_minor),
                membership_discount=to_major(line.membership_discount_minor) or None,
                grading_fee=to_major(line.grading_fee_minor),
                per_card_fees=to_major(line.per_card_fees_minor) or None,
                declared_value_fee=to_major(line.declared_value_fee_minor) or None,
                allocated_overhead=to_major(line.allocated_overhead_minor),
                total_cost=to_major(line.total_minor),
                allocation_weight=line.allocation_weight,
                predicted_grade=line.predicted_grade,
                actual_grade=line.actual_grade,
                status=line.status,
                sort_order=line.sort_order,
                blockers=line.blockers,
            )
            for line in costing.cards
        ],
        blockers=costing.blockers,
        warnings=costing.warnings,
    )


def _placed_out(card: optimiser.PlacedCard) -> PlacedCardOut:
    return PlacedCardOut(
        card_id=card.card_id,
        name=card.name,
        set_label=card.set_label,
        company_code=card.company_code,
        tier_id=card.tier_id,
        tier_name=card.tier_name,
        declared_value=card.declared_value,
        decision_when_routed=card.decision_when_routed,
        decision_in_batch=card.decision_in_batch,
        expected_profit=card.expected_profit,
        grading_cost=card.grading_cost,
        opportunity_score=card.opportunity_score,
        still_pays=card.still_pays,
        reason=card.reason,
        cheaper_tier_name=card.cheaper_tier_name,
        cheaper_tier_saving=card.cheaper_tier_saving,
    )


# --- Lookup ------------------------------------------------------------------


def _get(db, submission_id: str) -> GradingSubmission:
    submission = db.get(GradingSubmission, submission_id)
    if submission is None:
        raise NotFoundError("Submission", submission_id)
    return submission


def _editable(submission: GradingSubmission) -> None:
    """Cards can only move while the parcel is still on your desk.

    Once it has shipped, its contents are a record of what you sent — editing
    them would rewrite history and break the Phase 8 comparison of predicted
    against actual.
    """
    if submission.status not in {SubmissionStatus.DRAFT.value, SubmissionStatus.PLANNED.value}:
        raise ConflictError(
            f"This submission is {submission.status.replace('_', ' ')}, so its cards can no "
            "longer be changed. What you sent is a record, not a draft.",
            {"status": submission.status},
        )


def _apply(db, submission: GradingSubmission, payload: SubmissionWrite) -> None:
    data = payload.model_dump(exclude_unset=True)

    for field_name, column in (
        ("shipping_out", "shipping_out_minor"),
        ("shipping_return", "shipping_return_minor"),
        ("handling", "handling_minor"),
        ("other_fees", "other_fees_minor"),
    ):
        if field_name in data:
            setattr(submission, column, to_minor(data.pop(field_name)) or 0)

    if data.get("company_id") and db.get(GradingCompany, data["company_id"]) is None:
        raise NotFoundError("Grading company", data["company_id"])
    if data.get("tier_id") and db.get(GradingTier, data["tier_id"]) is None:
        raise NotFoundError("Grading tier", data["tier_id"])

    if data.get("status") and data["status"] not in {item.value for item in SubmissionStatus}:
        raise ConflictError(f"'{data['status']}' is not a submission status.")

    # The lifecycle dates are Date columns and arrive as ISO strings, so they
    # need parsing rather than assigning. Without this the column driver rejects
    # the string and a perfectly reasonable PATCH fails with a 500.
    for field_name in ("submitted_at", "received_at", "returned_at"):
        if field_name in data and isinstance(data[field_name], str):
            try:
                data[field_name] = date.fromisoformat(data[field_name][:10])
            except ValueError as exc:
                raise ConflictError(
                    f"'{data[field_name]}' is not a date. Use YYYY-MM-DD."
                ) from exc

    method = data.get("cost_allocation_method")
    if method and method not in {item.value for item in CostAllocationMethod}:
        raise ConflictError(f"'{method}' is not a cost allocation method.")

    moved_grader = bool(data.get("company_id")) and data["company_id"] != submission.company_id

    for key, value in data.items():
        if hasattr(submission, key):
            setattr(submission, key, value)

    if moved_grader and submission.status == SubmissionStatus.DRAFT.value:
        # Re-take the predictions against the grader the parcel is now going to.
        # A PSA prediction is not a CGC one, and nothing has been sent yet, so
        # nothing is being scored — this is still the prediction you hold.
        company = db.get(GradingCompany, submission.company_id)
        for row in submission.cards:
            row.predicted_grade, row.predicted_probabilities = submissions.predicted_grade_for(
                db, row.card_id, company
            )


# --- Routes ------------------------------------------------------------------


@router.get("", response_model=list[SubmissionOut], summary="List submissions")
def list_submissions(db: DbSession) -> list[SubmissionOut]:
    # Each row is fully costed rather than returned as a stub: the cost of a
    # parcel is the reason to open it, and a list of references with no numbers
    # would make the user click through every one to find that out.
    rows = db.scalars(
        select(GradingSubmission).order_by(GradingSubmission.created_at.desc())
    ).all()
    return [_out(db, submission) for submission in rows]


@router.post(
    "",
    response_model=SubmissionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a submission",
)
def create_submission(db: DbSession, payload: SubmissionCreate) -> SubmissionOut:
    if db.get(GradingCompany, payload.company_id) is None:
        raise NotFoundError("Grading company", payload.company_id)

    submission = GradingSubmission(
        reference=submissions.next_reference(db),
        company_id=payload.company_id,
    )
    db.add(submission)
    db.flush()
    _apply(db, submission, payload)

    company = db.get(GradingCompany, payload.company_id)
    for index, card_id in enumerate(payload.card_ids):
        if db.get(Card, card_id) is None:
            raise NotFoundError("Card", card_id)
        # Frozen now, not read back later: the prediction worth scoring is the
        # one you held when you sent the card.
        likely, distribution = submissions.predicted_grade_for(db, card_id, company)
        db.add(
            SubmissionCard(
                submission_id=submission.id,
                card_id=card_id,
                tier_id=payload.tier_id,
                sort_order=index,
                predicted_grade=likely,
                predicted_probabilities=distribution,
            )
        )

    db.commit()
    db.refresh(submission)
    return _out(db, submission)


@router.get("/{submission_id}", response_model=SubmissionOut, summary="One submission, costed")
def read_submission(db: DbSession, submission_id: str) -> SubmissionOut:
    return _out(db, _get(db, submission_id))


@router.patch("/{submission_id}", response_model=SubmissionOut, summary="Update a submission")
def update_submission(
    db: DbSession, submission_id: str, payload: SubmissionWrite
) -> SubmissionOut:
    submission = _get(db, submission_id)
    _apply(db, submission, payload)
    db.commit()
    db.refresh(submission)
    return _out(db, submission)


@router.delete(
    "/{submission_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a submission"
)
def delete_submission(db: DbSession, submission_id: str) -> None:
    submission = _get(db, submission_id)
    if submission.status not in {SubmissionStatus.DRAFT.value, SubmissionStatus.CANCELLED.value}:
        raise ConflictError(
            "Only a draft or cancelled submission can be deleted. Cancel it instead, so the "
            "record of what you sent survives.",
            {"status": submission.status},
        )
    db.delete(submission)
    db.commit()


@router.post(
    "/{submission_id}/cards",
    response_model=SubmissionOut,
    summary="Add cards to a submission",
)
def add_cards(db: DbSession, submission_id: str, payload: CardAdd) -> SubmissionOut:
    submission = _get(db, submission_id)
    _editable(submission)

    existing = {row.card_id for row in submission.cards}
    next_order = max((row.sort_order for row in submission.cards), default=-1) + 1
    company = db.get(GradingCompany, submission.company_id) if submission.company_id else None

    for card_id in payload.card_ids:
        if db.get(Card, card_id) is None:
            raise NotFoundError("Card", card_id)
        if card_id in existing:
            continue
        likely, distribution = submissions.predicted_grade_for(db, card_id, company)
        db.add(
            SubmissionCard(
                submission_id=submission.id,
                card_id=card_id,
                tier_id=payload.tier_id or submission.tier_id,
                sort_order=next_order,
                predicted_grade=likely,
                predicted_probabilities=distribution,
            )
        )
        next_order += 1

    db.commit()
    db.refresh(submission)
    return _out(db, submission)


@router.patch(
    "/{submission_id}/cards/{submission_card_id}",
    response_model=SubmissionOut,
    summary="Update one card's line",
)
def update_card(
    db: DbSession, submission_id: str, submission_card_id: str, payload: CardUpdate
) -> SubmissionOut:
    submission = _get(db, submission_id)
    row = db.get(SubmissionCard, submission_card_id)
    if row is None or row.submission_id != submission.id:
        raise NotFoundError("Submission card", submission_card_id)

    data = payload.model_dump(exclude_unset=True)
    if "declared_value" in data:
        # A value set here is the user's own, and the source records that so a
        # later recompute cannot quietly replace it.
        row.declared_value_minor = to_minor(data.pop("declared_value"))
        row.declared_value_source = "user"
        row.declared_value_confidence = "high"
    if data.get("status") and data["status"] not in {
        item.value for item in SubmissionCardStatus
    }:
        raise ConflictError(f"'{data['status']}' is not a submission card status.")
    if data.get("tier_id") and db.get(GradingTier, data["tier_id"]) is None:
        raise NotFoundError("Grading tier", data["tier_id"])

    for key, value in data.items():
        setattr(row, key, value)

    # Recording the grade is what closes the learning loop, so it happens here
    # rather than waiting for the user to press something. Idempotent, so
    # correcting a mistyped grade corrects its score too.
    db.flush()
    calibration.record_results_for_submission(db, submission)

    db.commit()
    db.refresh(submission)
    return _out(db, submission)


@router.delete(
    "/{submission_id}/cards/{submission_card_id}",
    response_model=SubmissionOut,
    summary="Remove a card from a submission",
)
def remove_card(db: DbSession, submission_id: str, submission_card_id: str) -> SubmissionOut:
    submission = _get(db, submission_id)
    _editable(submission)
    row = db.get(SubmissionCard, submission_card_id)
    if row is None or row.submission_id != submission.id:
        raise NotFoundError("Submission card", submission_card_id)

    db.delete(row)
    db.commit()
    db.refresh(submission)
    return _out(db, submission)


@router.post(
    "/optimise",
    response_model=OptimiserOut,
    summary="Pack the collection into submissions that still pay once packed",
)
def optimise_submissions(
    db: DbSession,
    limit: Annotated[
        int,
        Query(ge=1, le=1000, description="Most-recently-updated ready cards to consider."),
    ] = optimiser.DEFAULT_LIMIT,
) -> OptimiserOut:
    result = optimiser.optimise(db, limit=limit)
    return OptimiserOut(
        status=result.status,
        reason=result.reason,
        currency=result.currency,
        analysable=result.analysable,
        worth_grading=result.worth_grading,
        placed=result.placed,
        total_cards=result.total_cards,
        truncated=result.truncated,
        routed_at_batch_size=result.routed_at_batch_size,
        expected_profit=result.expected_profit,
        total_grading_cost=result.total_grading_cost,
        batches=[
            ProposedBatchOut(
                company_id=batch.company_id,
                company_code=batch.company_code,
                tier_id=batch.tier_id,
                tier_name=batch.tier_name,
                effective_tier_name=batch.effective_tier_name,
                card_count=batch.card_count,
                minimum_cards=batch.minimum_cards,
                maximum_cards=batch.maximum_cards,
                short_by=batch.short_by,
                expected_profit=batch.expected_profit,
                grading_cost=batch.grading_cost,
                expected_profit_if_filled=batch.expected_profit_if_filled,
                viable=batch.viable,
                reason=batch.reason,
                cards=[_placed_out(card) for card in batch.cards],
            )
            for batch in result.batches
        ],
        unplaced=[
            UnplacedCardOut(
                card_id=card.card_id,
                name=card.name,
                set_label=card.set_label,
                company_code=card.company_code,
                tier_name=card.tier_name,
                expected_profit=card.expected_profit,
                reason=card.reason,
            )
            for card in result.unplaced
        ],
        stopped_paying=[_placed_out(card) for card in result.stopped_paying],
        notes=result.notes,
    )
