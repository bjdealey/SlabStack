"""Real submissions: what a batch actually costs, and whether it is valid.

Phase 4 costed a *hypothetical* card in a hypothetical batch — "if you sent
twenty-five of these". This module costs the batch you actually built, from the
cards actually in it, and that changes three things:

**Insurance is charged on the parcel.** Twenty-five copies of a £200 card and
twenty-five cards averaging £200 insure for the same amount, but a batch holding
one £900 card and twenty-four £20 commons does not. The pot comes from the real
declared values.

**Allocation can finally be value-weighted.** Splitting shipping equally puts
the same £1.60 on a £900 alternate art and a £4 common. Weighting by declared
value is what most people mean by fair, and Phase 4 could not offer it because
it had no other cards to weight against. Here it does — this is where the
setting stops being a promise.

**Minimums are per tier, not per parcel.** Bulk pricing needs N cards *at bulk
rates*; a parcel of thirty holding three bulk cards does not qualify for bulk.
Shipping, though, is shared across the whole parcel regardless of tier — which
is exactly what makes a mixed submission worth building: adding cheap cards to
an expensive submission dilutes everyone's share of the postage.

Nothing here silently drops a card. A batch that breaks a tier's rules is
returned costed, with every violation named, because "you are four cards short
of Bulk" is a thing you can act on and a disappearing card is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import CostAllocationMethod, DeclaredValueSource
from app.models import (
    Card,
    GradingCompany,
    GradingMembership,
    GradingSubmission,
    GradingTier,
    MarketPrice,
    SubmissionCard,
)
from app.money import allocate, apply_pct, format_money
from app.services import (
    cards_service,
    economics,
    prediction_service,
    settings_service,
)

__all__ = [
    "CardLine",
    "SubmissionCosting",
    "TierGroup",
    "cost_submission",
    "declared_value_for",
]


@dataclass
class CardLine:
    """One card's line in a real submission, with its share of everything shared."""

    submission_card_id: str
    card_id: str
    name: str
    set_label: str | None = None
    tier_id: str | None = None
    tier_name: str | None = None

    declared_value_minor: int | None = None
    declared_value_source: str = DeclaredValueSource.SYSTEM.value
    declared_value_confidence: str | None = None

    base_fee_minor: int = 0
    membership_discount_minor: int = 0
    grading_fee_minor: int = 0
    per_card_fees_minor: int = 0
    declared_value_fee_minor: int = 0
    allocated_overhead_minor: int = 0
    total_minor: int = 0

    #: What drove this card's share of the shared pot. Equal allocation gives
    #: every card a weight of 1; value-weighted uses the declared value.
    allocation_weight: int = 1
    predicted_grade: float | None = None
    actual_grade: float | None = None
    status: str = "planned"
    sort_order: int = 0
    blockers: list[str] = field(default_factory=list)


@dataclass
class TierGroup:
    """The cards on one tier, and whether that tier's rules are satisfied.

    Grouped per tier because that is the unit a minimum applies to: thirty cards
    in a parcel with three of them at Bulk is three bulk cards, not thirty.
    """

    tier_id: str | None
    tier_name: str | None
    company_code: str
    card_count: int = 0
    minimum_cards: int = 1
    maximum_cards: int | None = None
    #: How many more cards this tier needs before its pricing applies.
    short_by: int = 0
    over_by: int = 0
    blockers: list[str] = field(default_factory=list)


@dataclass
class SubmissionCosting:
    """What this submission costs, split every way the user might ask."""

    submission_id: str
    reference: str
    name: str | None
    status: str
    currency: str = "GBP"
    company_id: str | None = None
    company_code: str | None = None
    company_name: str | None = None

    card_count: int = 0
    declared_value_total_minor: int = 0

    shipping_out_minor: int = 0
    shipping_return_minor: int = 0
    insurance_minor: int = 0
    handling_minor: int = 0
    other_fees_minor: int = 0
    tier_additional_fees_minor: int = 0
    shared_pot_minor: int = 0

    grading_fees_minor: int = 0
    per_card_fees_minor: int = 0
    declared_value_fees_minor: int = 0
    membership_discount_minor: int = 0
    total_minor: int = 0

    allocation_method: str = CostAllocationMethod.EQUAL.value
    allocation_note: str | None = None
    membership_code: str | None = None

    tiers: list[TierGroup] = field(default_factory=list)
    cards: list[CardLine] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def cost_per_card_minor(self) -> int | None:
        """Only meaningful as an average, and meaningless with no cards."""
        if not self.card_count:
            return None
        return round(self.total_minor / self.card_count)


def declared_value_for(
    db: Session,
    card: Card,
    submission_card: SubmissionCard | None,
    *,
    company: GradingCompany | None,
    settings_values: dict,
) -> tuple[int | None, str, str | None]:
    """This card's declared value: the user's, then the stored one, then computed.

    The user's own figure wins and is never overwritten by a recompute — spec
    section 13 keeps the suggestion and the decision apart. A value stored on the
    submission line is preserved too: once a parcel is costed, re-costing it
    should not quietly move the number the insurance was based on.
    """
    if card.user_declared_value_minor is not None:
        return card.user_declared_value_minor, DeclaredValueSource.USER.value, "high"

    if submission_card is not None and submission_card.declared_value_minor is not None:
        return (
            submission_card.declared_value_minor,
            submission_card.declared_value_source,
            submission_card.declared_value_confidence,
        )

    prices = list(
        db.scalars(select(MarketPrice).where(MarketPrice.catalog_key == card.catalog_key))
    )
    probabilities = _probabilities_for(db, card, company, settings_values)
    suggestion = economics.suggest_declared_value(
        card,
        prices=prices,
        probabilities=probabilities,
        company_code=company.code if company else None,
    )
    return suggestion.value_minor, suggestion.source, suggestion.confidence


def _probabilities_for(
    db: Session, card: Card, company: GradingCompany | None, settings_values: dict
) -> dict[float, float] | None:
    """The grade distribution used to weight a declared value, or ``None``.

    Predicted against the *same* company the submission is going to: a declared
    value weighted by PSA's ladder describes a card you are not sending.
    """
    if company is None:
        return None
    assessment = cards_service.current_condition(db, card.id)
    if assessment is None:
        return None
    try:
        prediction = prediction_service.predict(
            assessment,
            company=company,
            rules=prediction_service.load_rules(db, company.id),
            params=prediction_service.ModelParameters.from_settings(settings_values),
        )
    except prediction_service.NotEnoughAssessmentError:
        return None
    return prediction.probabilities


def _held_membership(company: GradingCompany, today: date) -> GradingMembership | None:
    return economics.held_membership(company, today)


def cost_submission(
    db: Session,
    submission: GradingSubmission,
    *,
    today: date | None = None,
) -> SubmissionCosting:
    """Cost a real batch from the cards actually in it."""
    today = today or date.today()
    settings_values = settings_service.get_all(db)
    company = db.get(GradingCompany, submission.company_id)
    membership = _held_membership(company, today) if company else None

    result = SubmissionCosting(
        submission_id=submission.id,
        reference=submission.reference,
        name=submission.name,
        status=submission.status,
        currency=submission.currency or settings_values.get("currency", "GBP"),
        company_id=submission.company_id,
        company_code=company.code if company else None,
        company_name=company.name if company else None,
        allocation_method=submission.cost_allocation_method,
        membership_code=membership.code if membership else None,
        shipping_out_minor=submission.shipping_out_minor,
        shipping_return_minor=submission.shipping_return_minor,
        handling_minor=submission.handling_minor,
        other_fees_minor=submission.other_fees_minor,
    )

    if company is None:
        result.blockers.append("This submission has no grading company.")
        return result

    rows = sorted(submission.cards, key=lambda item: (item.sort_order, item.created_at))
    if not rows:
        result.blockers.append(
            "No cards in this submission yet. Add the cards you intend to send."
        )
        return result

    default_tier = (
        db.get(GradingTier, submission.tier_id) if submission.tier_id else None
    )
    tiers_by_id: dict[str, GradingTier] = {}

    # --- Per-card fees, and the declared values the pot is built from --------
    lines: list[CardLine] = []
    for row in rows:
        card = db.get(Card, row.card_id)
        if card is None:  # pragma: no cover - the FK cascade makes this unreachable
            continue

        tier = db.get(GradingTier, row.tier_id) if row.tier_id else default_tier
        if tier is not None:
            tiers_by_id[tier.id] = tier

        value, source, confidence = declared_value_for(
            db,
            card,
            row,
            company=company,
            settings_values=settings_values,
        )

        line = CardLine(
            submission_card_id=row.id,
            card_id=card.id,
            name=_display_name(card),
            set_label=_set_label(card),
            tier_id=tier.id if tier else None,
            tier_name=tier.tier_name if tier else None,
            declared_value_minor=value,
            declared_value_source=source,
            declared_value_confidence=confidence,
            predicted_grade=row.predicted_grade,
            actual_grade=row.actual_grade,
            status=row.status,
            sort_order=row.sort_order,
        )

        if tier is None:
            line.blockers.append(
                "No tier chosen for this card, and the submission has no default tier."
            )
        else:
            discount_pct = tier.membership_discount_pct if membership else 0.0
            if membership and membership.discount_pct > discount_pct:
                discount_pct = membership.discount_pct
            line.base_fee_minor = tier.price_minor
            line.membership_discount_minor = (
                apply_pct(tier.price_minor, discount_pct) if discount_pct else 0
            )
            line.grading_fee_minor = line.base_fee_minor - line.membership_discount_minor
            line.per_card_fees_minor = tier.per_card_fees_minor
            if tier.declared_value_fee_pct and value:
                line.declared_value_fee_minor = apply_pct(value, tier.declared_value_fee_pct)
            if tier.price_minor <= 0:
                line.blockers.append(
                    f"No price configured for {company.code} {tier.tier_name}. "
                    "Add current pricing in Settings → Grading."
                )
            _check_value_ceiling(line, tier, company)

        if value is None:
            line.blockers.append(
                "No declared value for this card — the parcel cannot be insured accurately. "
                "Add comparable sales or set your own figure."
            )

        lines.append(line)

    result.card_count = len(lines)
    result.declared_value_total_minor = sum(
        line.declared_value_minor or 0 for line in lines
    )

    # --- The shared pot, from the real parcel --------------------------------
    insurance_pct = float(settings_values.get("default_submission_insurance_pct", 0.0) or 0.0)
    result.insurance_minor = (
        apply_pct(result.declared_value_total_minor, insurance_pct)
        if insurance_pct and result.declared_value_total_minor
        else 0
    )
    # A per-submission tier fee is charged once per tier used, not once per card.
    result.tier_additional_fees_minor = sum(
        tier.additional_fees_minor for tier in tiers_by_id.values()
    )
    result.shared_pot_minor = (
        result.shipping_out_minor
        + result.shipping_return_minor
        + result.insurance_minor
        + result.handling_minor
        + result.other_fees_minor
        + result.tier_additional_fees_minor
        + submission.membership_allocation_minor
    )

    # --- Allocation ----------------------------------------------------------
    weights, method, note = _allocation_weights(lines, submission.cost_allocation_method)
    result.allocation_method = method
    result.allocation_note = note
    shares = allocate(result.shared_pot_minor, weights) if result.shared_pot_minor else [0] * len(lines)

    for line, weight, share in zip(lines, weights, shares, strict=True):
        line.allocation_weight = weight
        line.allocated_overhead_minor = share
        line.total_minor = (
            line.grading_fee_minor
            + line.per_card_fees_minor
            + line.declared_value_fee_minor
            + share
        )

    result.cards = lines
    result.grading_fees_minor = sum(line.grading_fee_minor for line in lines)
    result.per_card_fees_minor = sum(line.per_card_fees_minor for line in lines)
    result.declared_value_fees_minor = sum(line.declared_value_fee_minor for line in lines)
    result.membership_discount_minor = sum(line.membership_discount_minor for line in lines)
    result.total_minor = sum(line.total_minor for line in lines)

    result.tiers = _tier_groups(lines, tiers_by_id, company)
    result.blockers = _submission_blockers(result, lines)
    result.warnings = _submission_warnings(result, company, membership)
    return result


def _check_value_ceiling(line: CardLine, tier: GradingTier, company: GradingCompany) -> None:
    """A card above a tier's ceiling is not covered for what it is worth."""
    value = line.declared_value_minor
    if value is None:
        return
    if tier.min_declared_value_minor and value < tier.min_declared_value_minor:
        line.blockers.append(
            f"Declared value {format_money(value)} is below {company.code} "
            f"{tier.tier_name}'s minimum of {format_money(tier.min_declared_value_minor)}."
        )
    if tier.max_declared_value_minor and value > tier.max_declared_value_minor:
        line.blockers.append(
            f"Declared value {format_money(value)} exceeds {company.code} "
            f"{tier.tier_name}'s ceiling of {format_money(tier.max_declared_value_minor)} — "
            "this card needs a more expensive tier."
        )


def _allocation_weights(
    lines: list[CardLine], method: str
) -> tuple[list[int], str, str | None]:
    """Weights for the shared pot, and an honest account of which method ran.

    Value-weighted allocation is what Phase 4 could only promise. It still falls
    back when it cannot be computed — a batch whose declared values are all
    unknown has nothing to weight by — and says so rather than producing an
    equal split under a value-weighted label.
    """
    if method == CostAllocationMethod.VALUE_WEIGHTED.value:
        values = [line.declared_value_minor or 0 for line in lines]
        if sum(values) > 0:
            return values, method, (
                "Shared costs are split by declared value, so the expensive cards carry "
                "more of the postage and insurance they are responsible for."
            )
        return (
            [1] * len(lines),
            CostAllocationMethod.EQUAL.value,
            "No card in this submission has a declared value, so there is nothing to weight "
            "by — shared costs are split equally instead.",
        )

    return (
        [1] * len(lines),
        CostAllocationMethod.EQUAL.value,
        "Shared costs are split equally across every card in the parcel.",
    )


def _tier_groups(
    lines: list[CardLine], tiers_by_id: dict[str, GradingTier], company: GradingCompany
) -> list[TierGroup]:
    """Count the cards on each tier and check that tier's own rules."""
    counts: dict[str | None, int] = {}
    for line in lines:
        counts[line.tier_id] = counts.get(line.tier_id, 0) + 1

    groups: list[TierGroup] = []
    for tier_id, count in counts.items():
        tier = tiers_by_id.get(tier_id) if tier_id else None
        group = TierGroup(
            tier_id=tier_id,
            tier_name=tier.tier_name if tier else None,
            company_code=company.code,
            card_count=count,
            minimum_cards=tier.minimum_cards if tier else 1,
            maximum_cards=tier.maximum_cards if tier else None,
        )
        if tier is None:
            group.blockers.append("These cards have no tier.")
        else:
            if count < tier.minimum_cards:
                group.short_by = tier.minimum_cards - count
                group.blockers.append(
                    f"{company.code} {tier.tier_name} needs {tier.minimum_cards} cards at that "
                    f"tier; this submission has {count}. Add {group.short_by} more, or move "
                    "them to a tier with no minimum."
                )
            if tier.maximum_cards is not None and count > tier.maximum_cards:
                group.over_by = count - tier.maximum_cards
                group.blockers.append(
                    f"{company.code} {tier.tier_name} takes at most {tier.maximum_cards} cards "
                    f"per submission; this has {count}. Split {group.over_by} into another "
                    "submission."
                )
            if tier.membership_required and not economics.held_membership(company):
                group.blockers.append(
                    f"{company.code} {tier.tier_name} requires a membership you do not hold."
                )
        groups.append(group)

    groups.sort(key=lambda item: (item.tier_name or ""))
    return groups


def _submission_blockers(result: SubmissionCosting, lines: list[CardLine]) -> list[str]:
    """Everything that would stop this parcel being sent as it stands."""
    blockers: list[str] = []
    for group in result.tiers:
        blockers.extend(group.blockers)

    unpriced = [line for line in lines if line.declared_value_minor is None]
    if unpriced:
        names = ", ".join(line.name for line in unpriced[:3])
        more = f" and {len(unpriced) - 3} more" if len(unpriced) > 3 else ""
        blockers.append(
            f"{len(unpriced)} card(s) have no declared value ({names}{more}). The parcel "
            "cannot be insured accurately until they do."
        )

    over_ceiling = [
        line for line in lines if any("ceiling" in item for item in line.blockers)
    ]
    if over_ceiling:
        blockers.append(
            f"{len(over_ceiling)} card(s) are worth more than their tier covers. Move them to "
            "a higher tier before sending."
        )
    return blockers


def _submission_warnings(
    result: SubmissionCosting,
    company: GradingCompany,
    membership: GradingMembership | None,
) -> list[str]:
    """Things worth knowing that do not stop the parcel going out."""
    warnings: list[str] = []

    if result.shared_pot_minor and result.card_count == 1:
        warnings.append(
            f"One card carries the whole {format_money(result.shared_pot_minor)} of shipping "
            "and insurance. Adding cards to this parcel lowers the cost of every card in it."
        )

    if membership is None:
        discountable = [t for t in company.tiers if t.active and t.membership_discount_pct > 0]
        available = [m for m in company.memberships if m.active]
        if discountable and available:
            saving = sum(
                apply_pct(line.base_fee_minor, discountable[0].membership_discount_pct)
                for line in result.cards
            )
            fee = available[0].annual_fee_minor
            if saving > 0:
                verdict = (
                    f"saves {format_money(saving - fee)} on this submission alone"
                    if saving > fee
                    else f"would need {format_money(fee - saving)} more of grading to break even"
                )
                warnings.append(
                    f"A {company.code} membership costs {format_money(fee)} a year and would "
                    f"take {format_money(saving)} off these fees — it {verdict}."
                )

    if result.allocation_method == CostAllocationMethod.EQUAL.value and result.card_count > 1:
        values = [line.declared_value_minor or 0 for line in result.cards]
        if values and max(values) and min(values) * 4 < max(values):
            warnings.append(
                "The cards in this parcel differ widely in value, and shared costs are split "
                "equally — the cheapest card carries as much postage as the most valuable. "
                "Value-weighted allocation is in Settings → Grading."
            )
    return warnings


def _display_name(card: Card) -> str:
    return f"{card.name} {card.card_number}" if card.card_number else card.name


def _set_label(card: Card) -> str | None:
    if card.set_name and card.set_code:
        return f"{card.set_name} ({card.set_code})"
    return card.set_name or card.set_code


def next_reference(db: Session, today: date | None = None) -> str:
    """A human-readable reference: SUB-2026-08-001.

    Sequential within the month rather than a UUID, because this is the thing
    the user writes on the parcel and quotes in an email.
    """
    today = today or date.today()
    prefix = f"SUB-{today:%Y-%m}-"
    existing = db.scalars(
        select(GradingSubmission.reference).where(GradingSubmission.reference.like(f"{prefix}%"))
    ).all()
    used = {
        int(reference.rsplit("-", 1)[-1])
        for reference in existing
        if reference.rsplit("-", 1)[-1].isdigit()
    }
    return f"{prefix}{(max(used) + 1 if used else 1):03d}"
