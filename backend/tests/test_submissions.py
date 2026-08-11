"""Costing a real batch.

The maths here is where pennies go missing and where a "fair" split quietly
stops being fair, so most of these tests are about the allocation rather than
the fees.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.enums import CostAllocationMethod
from app.models import Card, GradingCompany, GradingSubmission, GradingTier, SubmissionCard
from app.services import settings_service, submissions


def company(db, code: str = "CGC") -> GradingCompany:
    return db.scalars(select(GradingCompany).where(GradingCompany.code == code)).one()


def tier(db, company_code: str, tier_name: str) -> GradingTier:
    return db.scalars(
        select(GradingTier)
        .join(GradingCompany)
        .where(GradingCompany.code == company_code, GradingTier.tier_name == tier_name)
    ).one()


def make_card(db, name: str, value: float | None) -> Card:
    card = Card(name=name, set_code="EVS", catalog_key=f"english|evs|{name.lower()}|-|unlimited")
    if value is not None:
        card.user_declared_value_minor = round(value * 100)
        card.user_raw_value_minor = round(value * 100)
    db.add(card)
    db.flush()
    return card


def make_submission(
    db,
    cards: list[Card],
    *,
    company_code: str = "CGC",
    tier_name: str = "Economy",
    method: str = CostAllocationMethod.EQUAL.value,
    shipping_out: int = 0,
    shipping_return: int = 0,
    handling: int = 0,
) -> GradingSubmission:
    grader = company(db, company_code)
    chosen = tier(db, company_code, tier_name)
    submission = GradingSubmission(
        reference=submissions.next_reference(db),
        company_id=grader.id,
        tier_id=chosen.id,
        cost_allocation_method=method,
        shipping_out_minor=shipping_out,
        shipping_return_minor=shipping_return,
        handling_minor=handling,
    )
    db.add(submission)
    db.flush()
    for index, card in enumerate(cards):
        db.add(
            SubmissionCard(submission_id=submission.id, card_id=card.id, sort_order=index)
        )
    db.flush()
    db.refresh(submission)
    return submission


# --- Allocation --------------------------------------------------------------


def test_the_shared_pot_is_split_without_losing_a_penny(seeded_db):
    """An odd pot across three cards gives someone the spare penny, and none vanish."""
    settings_service.set_many(seeded_db, {"default_submission_insurance_pct": 0.0})
    cards = [make_card(seeded_db, f"Card {i}", 50.0) for i in range(3)]
    submission = make_submission(seeded_db, cards, shipping_out=1000)

    costing = submissions.cost_submission(seeded_db, submission)
    shares = [line.allocated_overhead_minor for line in costing.cards]
    assert costing.shared_pot_minor == 1000
    assert sum(shares) == 1000
    assert sorted(shares) == [333, 333, 334]


def test_insurance_is_in_the_pot_by_default(seeded_db):
    """The shipped default insures the parcel, so a bare postage figure is not the pot."""
    cards = [make_card(seeded_db, f"Card {i}", 50.0) for i in range(3)]
    submission = make_submission(seeded_db, cards, shipping_out=1000)

    costing = submissions.cost_submission(seeded_db, submission)
    assert costing.insurance_minor > 0
    assert costing.shared_pot_minor == 1000 + costing.insurance_minor
    assert sum(line.allocated_overhead_minor for line in costing.cards) == costing.shared_pot_minor


def test_value_weighted_allocation_makes_the_expensive_card_carry_more(seeded_db):
    """The whole point of the setting: a £4 common should not pay a £900 card's postage."""
    cheap = make_card(seeded_db, "Common", 4.0)
    dear = make_card(seeded_db, "Alt Art", 900.0)
    submission = make_submission(
        seeded_db,
        [cheap, dear],
        method=CostAllocationMethod.VALUE_WEIGHTED.value,
        shipping_out=2000,
    )

    costing = submissions.cost_submission(seeded_db, submission)
    by_name = {line.name: line for line in costing.cards}
    assert costing.allocation_method == "value_weighted"
    assert by_name["Alt Art"].allocated_overhead_minor > by_name["Common"].allocated_overhead_minor
    assert (
        by_name["Alt Art"].allocated_overhead_minor + by_name["Common"].allocated_overhead_minor
        == costing.shared_pot_minor
    ), "still penny-exact when weighted"


def test_value_weighted_with_nothing_to_weight_by_says_so(seeded_db):
    """An equal split under a value-weighted label would be a lie."""
    cards = [make_card(seeded_db, f"Unknown {i}", None) for i in range(2)]
    submission = make_submission(
        seeded_db, cards, method=CostAllocationMethod.VALUE_WEIGHTED.value, shipping_out=1000
    )

    costing = submissions.cost_submission(seeded_db, submission)
    assert costing.allocation_method == "equal", "it fell back"
    assert "nothing to weight" in costing.allocation_note


def test_equal_allocation_warns_when_the_cards_are_wildly_different(seeded_db):
    cards = [make_card(seeded_db, "Common", 4.0), make_card(seeded_db, "Alt Art", 900.0)]
    submission = make_submission(seeded_db, cards, shipping_out=2000)

    costing = submissions.cost_submission(seeded_db, submission)
    assert any("differ widely in value" in item for item in costing.warnings)


# --- The pot -----------------------------------------------------------------


def test_insurance_comes_from_the_real_declared_values(seeded_db):
    """Not one card's value multiplied by the batch, which is what a hypothetical batch assumed."""
    settings_service.set_many(seeded_db, {"default_submission_insurance_pct": 2.0})
    cards = [make_card(seeded_db, "Big", 900.0), make_card(seeded_db, "Small", 100.0)]
    submission = make_submission(seeded_db, cards)

    costing = submissions.cost_submission(seeded_db, submission)
    assert costing.declared_value_total_minor == 100_000
    assert costing.insurance_minor == 2000, "2% of the parcel, not 2% of a card, doubled"


def test_a_tier_fee_is_charged_once_per_tier_not_once_per_card(seeded_db):
    economy = tier(seeded_db, "CGC", "Economy")
    economy.additional_fees_minor = 500
    seeded_db.flush()

    cards = [make_card(seeded_db, f"Card {i}", 50.0) for i in range(4)]
    submission = make_submission(seeded_db, cards)

    costing = submissions.cost_submission(seeded_db, submission)
    assert costing.tier_additional_fees_minor == 500, "one parcel, one handling charge"


def test_a_single_card_is_told_it_is_carrying_the_whole_parcel(seeded_db):
    card = make_card(seeded_db, "Lonely", 200.0)
    submission = make_submission(seeded_db, [card], shipping_out=2000, shipping_return=2000)

    costing = submissions.cost_submission(seeded_db, submission)
    assert any("Adding cards to this parcel" in item for item in costing.warnings)


# --- Validation --------------------------------------------------------------


def test_a_tier_minimum_counts_cards_at_that_tier_not_in_the_parcel(seeded_db):
    """Thirty cards with three at Bulk is three bulk cards."""
    bulk = tier(seeded_db, "CGC", "Bulk")
    economy = tier(seeded_db, "CGC", "Economy")

    cards = [make_card(seeded_db, f"Card {i}", 50.0) for i in range(30)]
    submission = make_submission(seeded_db, cards, tier_name="Economy")
    for row in submission.cards[:3]:
        row.tier_id = bulk.id
    seeded_db.flush()
    seeded_db.refresh(submission)

    costing = submissions.cost_submission(seeded_db, submission)
    groups = {group.tier_name: group for group in costing.tiers}
    assert groups["Bulk"].card_count == 3
    assert groups["Bulk"].short_by == bulk.minimum_cards - 3
    assert groups["Economy"].card_count == 27
    assert not groups["Economy"].blockers
    assert any("needs" in item and "Bulk" in item for item in costing.blockers)
    assert economy.minimum_cards == 1, "the fixture assumption this test rests on"


def test_a_card_worth_more_than_its_tier_covers_is_named(seeded_db):
    economy = tier(seeded_db, "CGC", "Economy")
    economy.max_declared_value_minor = 20_000
    seeded_db.flush()

    cards = [make_card(seeded_db, "Cheap", 50.0), make_card(seeded_db, "Expensive", 900.0)]
    submission = make_submission(seeded_db, cards)

    costing = submissions.cost_submission(seeded_db, submission)
    expensive = next(line for line in costing.cards if line.name == "Expensive")
    assert any("ceiling" in item for item in expensive.blockers)
    assert any("worth more than their tier covers" in item for item in costing.blockers)


def test_a_card_with_no_declared_value_blocks_the_parcel_being_insured(seeded_db):
    cards = [make_card(seeded_db, "Known", 50.0), make_card(seeded_db, "Unknown", None)]
    submission = make_submission(seeded_db, cards)

    costing = submissions.cost_submission(seeded_db, submission)
    assert any("cannot be insured accurately" in item for item in costing.blockers)
    assert "Unknown" in " ".join(costing.blockers)


def test_an_empty_submission_says_so_rather_than_costing_nothing(seeded_db):
    submission = make_submission(seeded_db, [])
    costing = submissions.cost_submission(seeded_db, submission)

    assert costing.card_count == 0
    assert costing.total_minor == 0
    assert costing.cost_per_card_minor is None, "an average of nothing is not zero"
    assert any("No cards in this submission" in item for item in costing.blockers)


# --- Fees --------------------------------------------------------------------


def test_the_totals_are_the_sum_of_the_lines(seeded_db):
    cards = [make_card(seeded_db, f"Card {i}", 100.0 * (i + 1)) for i in range(4)]
    submission = make_submission(
        seeded_db, cards, shipping_out=1500, shipping_return=1500, handling=250
    )

    costing = submissions.cost_submission(seeded_db, submission)
    assert costing.total_minor == sum(line.total_minor for line in costing.cards)
    assert costing.total_minor == (
        costing.grading_fees_minor
        + costing.per_card_fees_minor
        + costing.declared_value_fees_minor
        + costing.shared_pot_minor
    )


def test_a_membership_you_hold_takes_its_discount_off_every_line(seeded_db):
    grader = company(seeded_db, "CGC")
    membership = next(m for m in grader.memberships if m.active)
    membership.user_holds = True
    economy = tier(seeded_db, "CGC", "Economy")
    economy.membership_discount_pct = 20.0
    seeded_db.flush()

    cards = [make_card(seeded_db, f"Card {i}", 100.0) for i in range(3)]
    submission = make_submission(seeded_db, cards)

    costing = submissions.cost_submission(seeded_db, submission)
    assert costing.membership_code == membership.code
    assert costing.membership_discount_minor > 0
    for line in costing.cards:
        assert line.grading_fee_minor == line.base_fee_minor - line.membership_discount_minor


def test_without_a_membership_the_break_even_is_spelled_out(seeded_db):
    economy = tier(seeded_db, "CGC", "Economy")
    economy.membership_discount_pct = 20.0
    seeded_db.flush()

    cards = [make_card(seeded_db, f"Card {i}", 100.0) for i in range(3)]
    submission = make_submission(seeded_db, cards)

    costing = submissions.cost_submission(seeded_db, submission)
    membership_note = next(
        (item for item in costing.warnings if "membership costs" in item), None
    )
    assert membership_note is not None
    assert "break even" in membership_note or "saves" in membership_note


def test_your_own_declared_value_is_never_overwritten(seeded_db):
    card = make_card(seeded_db, "Pinned", 250.0)
    submission = make_submission(seeded_db, [card])

    costing = submissions.cost_submission(seeded_db, submission)
    line = costing.cards[0]
    assert line.declared_value_minor == 25_000
    assert line.declared_value_source == "user"


@pytest.mark.parametrize("count", [1, 2, 7, 25, 100])
def test_allocation_is_penny_exact_at_every_size(seeded_db, count: int):
    cards = [make_card(seeded_db, f"Card {i}", 10.0 + i) for i in range(count)]
    submission = make_submission(
        seeded_db,
        cards,
        method=CostAllocationMethod.VALUE_WEIGHTED.value,
        shipping_out=1999,
        shipping_return=777,
    )

    costing = submissions.cost_submission(seeded_db, submission)
    assert sum(line.allocated_overhead_minor for line in costing.cards) == costing.shared_pot_minor
