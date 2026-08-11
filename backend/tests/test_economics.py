"""Grading costs and net sale value, tested without a database where possible.

The arithmetic that decides whether a submission is worth sending should be
checkable without an HTTP client, so the cost and net-value functions are pure
and get exercised directly.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.enums import Confidence, CostAllocationMethod
from app.models import (
    Card,
    GradingCompany,
    GradingMembership,
    GradingTier,
    MarketPrice,
    SellingCostProfile,
)
from app.money import to_minor
from app.services import economics
from app.services.economics import SubmissionAssumptions

TODAY = date(2026, 8, 11)


def tier(**overrides) -> GradingTier:
    defaults = {
        "tier_code": "standard",
        "tier_name": "Standard",
        "price_minor": to_minor(25.0),
        "currency": "GBP",
        "minimum_cards": 1,
        "maximum_cards": None,
        "min_declared_value_minor": None,
        "max_declared_value_minor": None,
        "membership_required": False,
        "membership_discount_pct": 0.0,
        "additional_fees_minor": 0,
        "per_card_fees_minor": 0,
        "declared_value_fee_pct": 0.0,
        "active": True,
        "sort_order": 10,
    }
    defaults.update(overrides)
    return GradingTier(**defaults)


def company(*tiers: GradingTier, memberships: list | None = None, code: str = "ACE") -> GradingCompany:
    row = GradingCompany(code=code, name=f"{code} Grading", currency="GBP")
    row.tiers = list(tiers)
    row.memberships = list(memberships or [])
    return row


def profile(**overrides) -> SellingCostProfile:
    defaults = {
        "code": "ebay_uk",
        "name": "eBay UK",
        "platform": "ebay",
        "currency": "GBP",
        "platform_fee_pct": 12.0,
        "payment_fee_pct": 0.0,
        "payment_fixed_fee_minor": 30,
        "listing_fee_minor": 0,
        "other_fee_pct": 0.0,
        "fees_apply_to_shipping": True,
        "shipping_charged_to_buyer_minor": 155,
        "shipping_cost_minor": 155,
        "packaging_cost_minor": 35,
        "graded_shipping_cost_minor": 550,
        "graded_packaging_cost_minor": 120,
        "is_default": True,
        "active": True,
    }
    defaults.update(overrides)
    return SellingCostProfile(**defaults)


def price(label: str, value: float, **overrides) -> MarketPrice:
    return MarketPrice(
        catalog_key="k",
        grade_label=label,
        currency="GBP",
        realistic_sale_minor=to_minor(value),
        median_minor=to_minor(value),
        sample_size=overrides.pop("sample_size", 10),
        confidence=overrides.pop("confidence", Confidence.MEDIUM.value),
        **overrides,
    )


# --- Declared value ----------------------------------------------------------


def test_declared_value_is_probability_weighted_not_the_best_case():
    """A card that is probably a 9 should not be declared at the 10 price."""
    prices = [price("raw", 100), price("PSA 10", 1000), price("PSA 9", 300)]
    result = economics.suggest_declared_value(
        Card(name="x"),
        prices=prices,
        probabilities={10.0: 0.2, 9.0: 0.8},
        company_code="PSA",
    )
    assert result.value_minor == to_minor(440)  # 0.2 * 1000 + 0.8 * 300
    assert result.value_minor < to_minor(1000)
    assert result.coverage == 1.0
    assert result.confidence == Confidence.HIGH.value


def test_uncovered_grades_are_unknown_not_worthless():
    """No price for grade 8 must not drag the estimate toward zero."""
    prices = [price("raw", 100), price("PSA 10", 1000)]
    result = economics.suggest_declared_value(
        Card(name="x"),
        prices=prices,
        probabilities={10.0: 0.5, 8.0: 0.5},
        company_code="PSA",
    )
    assert result.value_minor == to_minor(1000), "renormalised over the covered half"
    assert result.coverage == 0.5
    assert result.confidence == Confidence.MEDIUM.value


def test_a_slab_is_never_declared_below_the_raw_card():
    prices = [price("raw", 500), price("PSA 6", 200)]
    result = economics.suggest_declared_value(
        Card(name="x"), prices=prices, probabilities={6.0: 1.0}, company_code="PSA"
    )
    assert result.value_minor == to_minor(500)


def test_declared_value_falls_back_to_raw_with_a_reason():
    result = economics.suggest_declared_value(
        Card(name="x"), prices=[price("raw", 210)], probabilities={10.0: 1.0}, company_code="PSA"
    )
    assert result.value_minor == to_minor(210)
    assert result.confidence == Confidence.LOW.value
    assert "no graded sales" in result.basis.lower()


def test_declared_value_falls_back_to_what_you_paid_last():
    card = Card(name="x", purchase_price_minor=to_minor(185))
    result = economics.suggest_declared_value(
        card, prices=[], probabilities=None, company_code=None
    )
    assert result.value_minor == to_minor(185)
    assert result.confidence == Confidence.NONE.value
    assert "floor" in result.basis


def test_no_value_anywhere_is_none_not_zero():
    result = economics.suggest_declared_value(
        Card(name="x"), prices=[], probabilities=None, company_code=None
    )
    assert result.value_minor is None
    assert result.confidence == Confidence.NONE.value


# --- Eligibility -------------------------------------------------------------


def test_an_unpriced_tier_is_reported_not_costed_at_zero():
    grader = company(tier(tier_code="bulk", tier_name="Bulk", price_minor=0))
    [(_, blockers)] = economics.eligible_tiers(
        grader, declared_value_minor=to_minor(100), batch_size=1, today=TODAY
    )
    assert any("No price configured" in reason for reason in blockers)


def test_a_batch_minimum_is_a_blocker_that_names_the_number():
    grader = company(tier(tier_code="bulk", minimum_cards=25))
    [(_, blockers)] = economics.eligible_tiers(
        grader, declared_value_minor=to_minor(100), batch_size=3, today=TODAY
    )
    assert blockers == ["Needs 25 cards in one submission; 3 assumed."]

    [(_, filled)] = economics.eligible_tiers(
        grader, declared_value_minor=to_minor(100), batch_size=25, today=TODAY
    )
    assert filled == []


def test_a_declared_value_ceiling_pushes_the_card_up_a_tier():
    grader = company(
        tier(tier_code="economy", max_declared_value_minor=to_minor(800)),
        tier(tier_code="standard", price_minor=to_minor(54), sort_order=20),
    )
    results = economics.eligible_tiers(
        grader, declared_value_minor=to_minor(1200), batch_size=1, today=TODAY
    )
    economy, standard = results
    assert any("ceiling" in reason for reason in economy[1])
    assert standard[1] == []


def test_a_tier_needing_a_membership_you_do_not_hold_says_what_it_costs():
    grader = company(
        tier(membership_required=True),
        memberships=[
            GradingMembership(
                code="collectors", name="Collectors Club",
                annual_fee_minor=to_minor(89), active=True, user_holds=False,
            )
        ],
    )
    [(_, blockers)] = economics.eligible_tiers(
        grader, declared_value_minor=to_minor(100), batch_size=1, today=TODAY
    )
    assert "£89.00/year" in blockers[0]


def test_an_expired_membership_does_not_count_as_held():
    grader = company(
        tier(membership_required=True),
        memberships=[
            GradingMembership(
                code="collectors", name="Collectors Club", annual_fee_minor=to_minor(89),
                active=True, user_holds=True, expires_on=TODAY - timedelta(days=1),
            )
        ],
    )
    [(_, blockers)] = economics.eligible_tiers(
        grader, declared_value_minor=to_minor(100), batch_size=1, today=TODAY
    )
    assert blockers, "an expired membership is not a membership"


def test_a_tier_out_of_its_effective_dates_is_not_offered():
    grader = company(tier(effective_to=TODAY - timedelta(days=1)))
    assert economics.eligible_tiers(
        grader, declared_value_minor=None, batch_size=1, today=TODAY
    ) == []


# --- Cost per card -----------------------------------------------------------


def test_the_batch_is_not_a_detail():
    """£40 of shipping across one card is £40; across twenty-five it is £1.60."""
    grader = company(tier(price_minor=to_minor(19)))
    assumptions = SubmissionAssumptions(
        shipping_out_minor=to_minor(20), shipping_return_minor=to_minor(20)
    )

    alone = economics.cost_for_tier(
        grader.tiers[0], grader, declared_value_minor=to_minor(200),
        assumptions=SubmissionAssumptions(**{**assumptions.__dict__, "batch_size": 1}),
    )
    batched = economics.cost_for_tier(
        grader.tiers[0], grader, declared_value_minor=to_minor(200),
        assumptions=SubmissionAssumptions(**{**assumptions.__dict__, "batch_size": 25}),
    )

    assert alone.cost.allocated_overhead_minor == to_minor(40)
    assert alone.cost.total_minor == to_minor(59)
    assert batched.cost.allocated_overhead_minor == to_minor(1.60)
    assert batched.cost.total_minor == to_minor(20.60)


def test_shared_costs_split_penny_exact():
    """The parts must sum back to the pot — no penny invented, none lost."""
    grader = company(tier(price_minor=to_minor(19)))
    assumptions = SubmissionAssumptions(batch_size=7, shipping_out_minor=1000)
    costing = economics.cost_for_tier(
        grader.tiers[0], grader, declared_value_minor=None, assumptions=assumptions
    )
    # 1000 / 7 does not divide; the first card carries the extra penny.
    assert costing.cost.allocated_overhead_minor == 143
    assert costing.cost.shared_total_minor == 1000


def test_a_membership_you_hold_takes_the_discount():
    grader = company(
        tier(price_minor=to_minor(50), membership_discount_pct=20.0),
        memberships=[
            GradingMembership(
                code="club", name="Club", annual_fee_minor=to_minor(89),
                active=True, user_holds=True, discount_pct=0.0,
            )
        ],
    )
    costing = economics.cost_for_tier(
        grader.tiers[0], grader, declared_value_minor=None,
        assumptions=SubmissionAssumptions(),
    )
    assert costing.cost.membership_discount_minor == to_minor(10)
    assert costing.cost.grading_fee_minor == to_minor(40)
    assert costing.membership_code == "club"


def test_a_percentage_of_declared_value_is_charged_where_the_grader_charges_it():
    grader = company(tier(price_minor=to_minor(25), declared_value_fee_pct=1.0))
    costing = economics.cost_for_tier(
        grader.tiers[0], grader, declared_value_minor=to_minor(1000),
        assumptions=SubmissionAssumptions(),
    )
    assert costing.cost.declared_value_fee_minor == to_minor(10)
    assert costing.cost.total_minor == to_minor(35)


def test_insurance_covers_the_whole_parcel_not_one_card():
    grader = company(tier(price_minor=to_minor(19)))
    costing = economics.cost_for_tier(
        grader.tiers[0], grader, declared_value_minor=to_minor(200),
        assumptions=SubmissionAssumptions(batch_size=10, insurance_pct=1.0),
    )
    # 1% of 10 * GBP 200 = GBP 20, split ten ways.
    assert costing.cost.shared_total_minor == to_minor(20)
    assert costing.cost.allocated_overhead_minor == to_minor(2)


def test_value_weighted_allocation_says_it_cannot_be_honoured_yet():
    assumptions = SubmissionAssumptions.from_settings(
        {"cost_allocation_method": CostAllocationMethod.VALUE_WEIGHTED.value}, batch_size=5
    )
    assert assumptions.allocation_method == CostAllocationMethod.EQUAL.value
    assert "needs a real batch" in assumptions.allocation_note


# --- Net sale value ----------------------------------------------------------


def test_net_sale_subtracts_every_cost_and_adds_the_buyers_postage():
    result = economics.net_sale_value(to_minor(100), profile(), graded=False)
    # Fee base 101.55 → platform 12.19, payment 0.30, postage 1.55, packaging 0.35
    assert result.platform_fee_minor == to_minor(12.19)
    assert result.payment_fee_minor == to_minor(0.30)
    assert result.net_minor == to_minor(87.16)
    assert result.shipping_income_minor == to_minor(1.55)


def test_a_slab_pays_the_graded_postage():
    raw = economics.net_sale_value(to_minor(100), profile(), graded=False)
    slab = economics.net_sale_value(to_minor(100), profile(), graded=True)
    assert slab.postage_cost_minor == to_minor(5.50)
    assert slab.packaging_cost_minor == to_minor(1.20)
    assert slab.net_minor < raw.net_minor


def test_a_platform_that_does_not_tax_postage_charges_less():
    taxed = economics.net_sale_value(to_minor(100), profile(fees_apply_to_shipping=True), graded=False)
    untaxed = economics.net_sale_value(
        to_minor(100), profile(fees_apply_to_shipping=False), graded=False
    )
    assert untaxed.platform_fee_minor < taxed.platform_fee_minor


def test_a_private_sale_keeps_almost_everything():
    private = profile(
        code="private", platform_fee_pct=0.0, payment_fixed_fee_minor=0,
        shipping_charged_to_buyer_minor=0, shipping_cost_minor=0, packaging_cost_minor=0,
        graded_shipping_cost_minor=0, graded_packaging_cost_minor=0,
    )
    result = economics.net_sale_value(to_minor(100), private, graded=True)
    assert result.net_minor == to_minor(100)


def test_no_price_and_no_profile_produce_no_number():
    assert economics.net_sale_value(None, profile(), graded=False) is None
    assert economics.net_sale_value(to_minor(100), None, graded=False) is None


def test_graded_postage_falls_back_to_raw_when_unset():
    """A profile with no graded figures should not silently post a slab free."""
    result = economics.net_sale_value(
        to_minor(100),
        profile(graded_shipping_cost_minor=None, graded_packaging_cost_minor=None),
        graded=True,
    )
    assert result.postage_cost_minor == to_minor(1.55)
    assert result.packaging_cost_minor == to_minor(0.35)


@pytest.mark.parametrize("gross", [0, 500, 12_345, 1_000_000])
def test_costs_and_net_always_reconcile(gross: int):
    result = economics.net_sale_value(gross, profile(), graded=True)
    assert result.net_minor + result.total_costs_minor == gross + result.shipping_income_minor
