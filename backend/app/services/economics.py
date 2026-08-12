"""What grading actually costs, and what a sale actually nets (spec sections 11-13, 21-23).

Two questions this module answers, both of them about money leaving the room
rather than money on paper:

**What does it cost to get this card graded?** Not the tier price on the
grader's website — that is the headline. The real number is the fee after any
membership discount, plus the per-card fees, plus the percentage some graders
charge on declared value, plus this card's share of shipping out, shipping
back, insurance and handling. Shipping £20 each way across one card is £40 on
that card; across twenty-five it is £1.60. The batch is not a detail.

**What does a sale actually net?** Gross minus platform fee, payment fee,
listing fee, postage and packaging — and a slab posts heavier and insured, so
it uses the graded postage figures. The buyer's postage contribution is income
and is taxed by the platform's percentage on most sites, which is why
``fees_apply_to_shipping`` is a column rather than an assumption.

Everything is integer minor units, and shared costs go through
``money.allocate`` so the parts always sum exactly back to the total.

The engine never invents a fee. A tier with no price configured is reported
unavailable with a reason, not costed at zero — an unpriced tier left active
would make every submission look free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import Confidence, CostAllocationMethod, DeclaredValueSource
from app.models import Card, GradingCompany, GradingMembership, GradingTier, SellingCostProfile
from app.money import allocate, apply_pct
from app.services import market_service
from app.services.identity import grade_label as build_grade_label

__all__ = [
    "CostBreakdown",
    "DeclaredValue",
    "NetSaleValue",
    "SubmissionAssumptions",
    "TierCosting",
    "cost_for_tier",
    "default_profile",
    "eligible_tiers",
    "net_sale_value",
    "suggest_declared_value",
]


# --- Declared value (spec section 13) ----------------------------------------


@dataclass
class DeclaredValue:
    """What to tell the grader the card is worth.

    Deliberately *not* the top-grade value. Declared value decides insurance
    and which tier the card is eligible for, so over-declaring pushes a card
    into a tier that costs more than it needs to, and under-declaring leaves it
    under-insured if the grader loses it. The honest figure is what the slab is
    realistically expected to be worth, which is the probability-weighted value
    across the grades it might come back as.
    """

    value_minor: int | None = None
    source: str = DeclaredValueSource.SYSTEM.value
    confidence: str = Confidence.NONE.value
    basis: str | None = None
    #: Share of the grade distribution covered by grades we have prices for.
    coverage: float | None = None


_FLOOR = (
    " A slab is worth at least the card inside it, so this is a floor rather than an estimate."
)


def _why_not_weighted(
    by_label: dict, probabilities: dict[float, float] | None, company_code: str | None
) -> str:
    """Say which thing is actually missing, because they need opposite actions.

    Falling back to the raw value has three quite different causes and they used
    to share one sentence — "no graded sales are stored for this card" — which
    was a claim about the data and was often simply untrue. A user reading it
    with a screen full of PSA 10 comparables in front of them would go and
    import graded sales they already had, and nothing would change.
    """
    graded = sorted(label for label in by_label if label != "raw")

    if not graded:
        return (
            "The raw market value — no graded sales are stored for this card, so there is "
            "nothing to estimate the slab's value from." + _FLOOR
        )

    stored = ", ".join(graded)
    if not probabilities:
        return (
            f"The raw market value. Graded sales are stored ({stored}), but weighting them "
            "needs to know how likely each grade is, and this card has not been assessed yet. "
            "Assess its condition and this becomes a probability-weighted figure." + _FLOOR
        )
    if not company_code:
        return (
            f"The raw market value. Graded sales are stored ({stored}), but no grading company "
            "is selected to value against." + _FLOOR
        )
    return (
        f"The raw market value. The stored graded sales ({stored}) are not on {company_code}'s "
        f"ladder, so none of them price a grade this card might come back as from {company_code}."
        + _FLOOR
    )


def suggest_declared_value(
    card: Card,
    *,
    prices: list,
    probabilities: dict[float, float] | None,
    company_code: str | None,
) -> DeclaredValue:
    """Suggest a declared value, and say what it was worked out from.

    Falls back down a ladder as the evidence thins, reporting a lower
    confidence at each step rather than switching silently:

    1. Probability-weighted graded value, over the grades we have prices for.
    2. The raw market value — a slab is worth at least the card inside it.
    3. The user's own raw estimate, or what they paid.
    """
    by_label = {row.grade_label: row for row in prices}
    raw_row = by_label.get("raw")
    raw_minor = None
    if raw_row is not None:
        raw_minor = raw_row.user_value_minor or raw_row.realistic_sale_minor or raw_row.median_minor

    if probabilities and company_code:
        weighted = 0.0
        covered = 0.0
        for grade, probability in probabilities.items():
            row = by_label.get(build_grade_label(company_code, float(grade)))
            if row is None:
                continue
            value = row.user_value_minor or row.realistic_sale_minor or row.median_minor
            if not value:
                continue
            weighted += value * probability
            covered += probability

        if covered > 0:
            # Renormalise over the covered mass: the grades we have no price
            # for are unknown, not worthless.
            value_minor = round(weighted / covered)
            # A slab is never worth less than the raw card inside it.
            if raw_minor and value_minor < raw_minor:
                value_minor = raw_minor
            confidence = (
                Confidence.HIGH.value
                if covered >= 0.8
                else Confidence.MEDIUM.value
                if covered >= 0.5
                else Confidence.LOW.value
            )
            return DeclaredValue(
                value_minor=value_minor,
                confidence=confidence,
                coverage=round(covered, 3),
                basis=(
                    f"Probability-weighted across the {company_code} grades with sales data, "
                    f"covering {covered:.0%} of the likely outcomes."
                ),
            )

    if raw_minor:
        return DeclaredValue(
            value_minor=raw_minor,
            confidence=Confidence.LOW.value,
            basis=_why_not_weighted(by_label, probabilities, company_code),
        )

    fallback = card.user_raw_value_minor or card.purchase_price_minor
    if fallback:
        return DeclaredValue(
            value_minor=fallback,
            confidence=Confidence.NONE.value,
            basis=(
                "Your own raw estimate."
                if card.user_raw_value_minor
                else "What you paid for it — a floor, not a valuation."
            ),
        )

    return DeclaredValue(
        basis="No value known for this card. Add comparable sales or your own estimate."
    )


# --- Tier eligibility (spec section 11) --------------------------------------


@dataclass
class SubmissionAssumptions:
    """The batch this card is imagined to travel in.

    Costing a single card means assuming a submission around it. The
    assumptions are returned alongside every figure so the user can see that
    "£23.40 per card" means "if you send twenty-five".
    """

    batch_size: int = 1
    shipping_out_minor: int = 0
    shipping_return_minor: int = 0
    insurance_pct: float = 0.0
    handling_minor: int = 0
    allocation_method: str = CostAllocationMethod.EQUAL.value
    #: Set when the requested allocation method could not be honoured.
    allocation_note: str | None = None

    @classmethod
    def from_settings(cls, values: dict, batch_size: int = 1) -> SubmissionAssumptions:
        method = values.get("cost_allocation_method", CostAllocationMethod.EQUAL.value)
        note = None
        if method == CostAllocationMethod.VALUE_WEIGHTED.value:
            # Weighting by value needs the other cards' values, which do not
            # exist until there is a real batch. Saying so beats quietly
            # producing a number from a different method.
            note = (
                "Value-weighted allocation needs a real batch to weight against, so these "
                "figures split shared costs equally. Build a submission to see your split."
            )
            method = CostAllocationMethod.EQUAL.value
        return cls(
            batch_size=max(1, int(batch_size)),
            shipping_out_minor=_money_setting(values, "default_submission_shipping_out"),
            shipping_return_minor=_money_setting(values, "default_submission_shipping_return"),
            insurance_pct=float(values.get("default_submission_insurance_pct", 0.0) or 0.0),
            allocation_method=method,
            allocation_note=note,
        )


def _money_setting(values: dict, key: str) -> int:
    """Settings store money in major units; the engine works in minor."""
    from app.money import to_minor

    return to_minor(values.get(key, 0) or 0) or 0


def held_membership(company: GradingCompany, today: date | None = None) -> GradingMembership | None:
    """The membership the user actually holds with this company, if unexpired."""
    today = today or date.today()
    for membership in company.memberships:
        if not (membership.active and membership.user_holds):
            continue
        if membership.expires_on is not None and membership.expires_on < today:
            continue
        return membership
    return None


def _tier_in_effect(tier: GradingTier, today: date) -> bool:
    """Grader pricing changes; a tier only applies inside its effective dates."""
    started = tier.effective_from is None or tier.effective_from <= today
    ended = tier.effective_to is not None and tier.effective_to < today
    return started and not ended


def eligible_tiers(
    company: GradingCompany,
    *,
    declared_value_minor: int | None,
    batch_size: int,
    today: date | None = None,
) -> list[tuple[GradingTier, list[str]]]:
    """Every tier for this company, each with the reasons it cannot be used.

    Returns tiers rather than filtering them out: "PSA Bulk needs twenty cards
    and you have three" is more useful than PSA Bulk silently disappearing.
    An empty reason list means the tier is usable.
    """
    today = today or date.today()
    membership = held_membership(company, today)
    results: list[tuple[GradingTier, list[str]]] = []

    for tier in sorted(company.tiers, key=lambda item: (item.sort_order, item.price_minor)):
        if not tier.active or not _tier_in_effect(tier, today):
            continue

        blockers: list[str] = []
        if tier.price_minor <= 0:
            blockers.append(
                f"No price configured for {company.code} {tier.tier_name}. "
                "Add current pricing in Settings → Grading."
            )

        if declared_value_minor is not None:
            if tier.min_declared_value_minor and declared_value_minor < tier.min_declared_value_minor:
                blockers.append(
                    f"Declared value is below this tier's minimum of "
                    f"{_pounds(tier.min_declared_value_minor)}."
                )
            if tier.max_declared_value_minor and declared_value_minor > tier.max_declared_value_minor:
                blockers.append(
                    f"Declared value exceeds this tier's ceiling of "
                    f"{_pounds(tier.max_declared_value_minor)} — a more expensive tier is required."
                )

        if batch_size < tier.minimum_cards:
            blockers.append(
                f"Needs {tier.minimum_cards} cards in one submission; "
                f"{batch_size} assumed."
            )
        if tier.maximum_cards is not None and batch_size > tier.maximum_cards:
            blockers.append(f"Takes at most {tier.maximum_cards} cards per submission.")

        if tier.membership_required and membership is None:
            available = [m for m in company.memberships if m.active]
            cost = f" ({_pounds(available[0].annual_fee_minor)}/year)" if available else ""
            blockers.append(f"Requires {company.code} membership{cost}, which you do not hold.")

        results.append((tier, blockers))

    return results


def _pounds(minor: int | None) -> str:
    from app.money import format_money

    return format_money(minor)


# --- Cost per card (spec section 12) -----------------------------------------


@dataclass
class CostBreakdown:
    """Where every penny of a card's grading cost goes."""

    base_fee_minor: int = 0
    membership_discount_minor: int = 0
    grading_fee_minor: int = 0
    per_card_fees_minor: int = 0
    declared_value_fee_minor: int = 0
    allocated_overhead_minor: int = 0
    total_minor: int = 0

    #: The shared pot before it was split, for "£40 across 25 cards" honesty.
    shared_total_minor: int = 0
    batch_size: int = 1


@dataclass
class TierCosting:
    tier: GradingTier
    company: GradingCompany
    cost: CostBreakdown
    blockers: list[str] = field(default_factory=list)
    membership_code: str | None = None

    @property
    def available(self) -> bool:
        return not self.blockers


def shared_pot(
    tier: GradingTier,
    *,
    assumptions: SubmissionAssumptions,
    declared_value_minor: int | None,
) -> int:
    """Costs that belong to the submission rather than to any one card."""
    total = tier.additional_fees_minor
    total += assumptions.shipping_out_minor
    total += assumptions.shipping_return_minor
    total += assumptions.handling_minor
    if assumptions.insurance_pct and declared_value_minor:
        # Insurance is charged on the parcel's whole declared value, so the
        # batch insures batch_size cards' worth.
        total += apply_pct(declared_value_minor * assumptions.batch_size, assumptions.insurance_pct)
    return total


def cost_for_tier(
    tier: GradingTier,
    company: GradingCompany,
    *,
    declared_value_minor: int | None,
    assumptions: SubmissionAssumptions,
    blockers: list[str] | None = None,
) -> TierCosting:
    """Total cost to grade one card at this tier, in a batch of ``batch_size``."""
    membership = held_membership(company)
    discount_pct = tier.membership_discount_pct if membership else 0.0
    if membership and membership.discount_pct > discount_pct:
        discount_pct = membership.discount_pct

    base = tier.price_minor
    discount = apply_pct(base, discount_pct) if discount_pct else 0
    fee = base - discount

    declared_fee = 0
    if tier.declared_value_fee_pct and declared_value_minor:
        declared_fee = apply_pct(declared_value_minor, tier.declared_value_fee_pct)

    pot = shared_pot(tier, assumptions=assumptions, declared_value_minor=declared_value_minor)
    # Equal split, penny-exact: this card gets the first share, and the parts
    # always sum back to the pot.
    share = allocate(pot, [1] * assumptions.batch_size)[0] if pot else 0

    breakdown = CostBreakdown(
        base_fee_minor=base,
        membership_discount_minor=discount,
        grading_fee_minor=fee,
        per_card_fees_minor=tier.per_card_fees_minor,
        declared_value_fee_minor=declared_fee,
        allocated_overhead_minor=share,
        total_minor=fee + tier.per_card_fees_minor + declared_fee + share,
        shared_total_minor=pot,
        batch_size=assumptions.batch_size,
    )
    return TierCosting(
        tier=tier,
        company=company,
        cost=breakdown,
        blockers=list(blockers or []),
        membership_code=membership.code if membership else None,
    )


# --- Net sale value (spec sections 21-23) ------------------------------------


@dataclass
class NetSaleValue:
    """What actually reaches your bank after a sale."""

    gross_minor: int
    shipping_income_minor: int = 0
    platform_fee_minor: int = 0
    payment_fee_minor: int = 0
    other_fee_minor: int = 0
    listing_fee_minor: int = 0
    postage_cost_minor: int = 0
    packaging_cost_minor: int = 0
    net_minor: int = 0
    profile_code: str | None = None
    is_graded: bool = False

    @property
    def total_costs_minor(self) -> int:
        return self.gross_minor + self.shipping_income_minor - self.net_minor


def default_profile(db: Session) -> SellingCostProfile | None:
    """The profile the engine sells through unless told otherwise."""
    stmt = select(SellingCostProfile).where(SellingCostProfile.active.is_(True))
    profiles = list(db.scalars(stmt.order_by(SellingCostProfile.sort_order)))
    for profile in profiles:
        if profile.is_default:
            return profile
    return profiles[0] if profiles else None


def profile_by_code(db: Session, code: str | None) -> SellingCostProfile | None:
    if not code:
        return None
    return db.scalar(select(SellingCostProfile).where(SellingCostProfile.code == code))


def net_sale_value(
    gross_minor: int | None,
    profile: SellingCostProfile | None,
    *,
    graded: bool,
) -> NetSaleValue | None:
    """Sale price in, money-in-hand out.

    ``graded`` picks the postage and packaging figures: a slab is heavier, goes
    tracked, and eats a chunk of the premium the slab earned. Using the raw
    postage figure for a graded sale is one of the quieter ways to make grading
    look more profitable than it is.
    """
    if gross_minor is None or profile is None:
        return None

    shipping_income = profile.shipping_charged_to_buyer_minor
    # Most platforms charge their percentage on the whole order, postage
    # included — small per card, material across a collection.
    fee_base = gross_minor + (shipping_income if profile.fees_apply_to_shipping else 0)

    platform_fee = apply_pct(fee_base, profile.platform_fee_pct)
    payment_fee = apply_pct(fee_base, profile.payment_fee_pct) + profile.payment_fixed_fee_minor
    other_fee = apply_pct(fee_base, profile.other_fee_pct)

    postage = profile.shipping_cost_minor
    packaging = profile.packaging_cost_minor
    if graded:
        postage = (
            profile.graded_shipping_cost_minor
            if profile.graded_shipping_cost_minor is not None
            else postage
        )
        packaging = (
            profile.graded_packaging_cost_minor
            if profile.graded_packaging_cost_minor is not None
            else packaging
        )

    net = (
        gross_minor
        + shipping_income
        - platform_fee
        - payment_fee
        - other_fee
        - profile.listing_fee_minor
        - postage
        - packaging
    )

    return NetSaleValue(
        gross_minor=gross_minor,
        shipping_income_minor=shipping_income,
        platform_fee_minor=platform_fee,
        payment_fee_minor=payment_fee,
        other_fee_minor=other_fee,
        listing_fee_minor=profile.listing_fee_minor,
        postage_cost_minor=postage,
        packaging_cost_minor=packaging,
        net_minor=net,
        profile_code=profile.code,
        is_graded=graded,
    )


def net_by_grade(
    summary: market_service.MarketSummary,
    profile: SellingCostProfile | None,
) -> dict[str, NetSaleValue]:
    """Net proceeds for every grade this card has a price for.

    Tier-independent: what a PSA 10 nets does not depend on which tier got it
    graded, so this is computed once rather than per option.
    """
    results: dict[str, NetSaleValue] = {}
    for row in summary.prices:
        gross = row.user_value_minor or row.realistic_sale_minor or row.median_minor
        value = net_sale_value(gross, profile, graded=row.grade_label != "raw")
        if value is not None:
            results[row.grade_label] = value
    return results
