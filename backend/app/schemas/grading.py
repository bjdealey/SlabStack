"""Grading company, tier, membership and selling-profile payloads."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.models import GradingCompany, GradingMembership, GradingTier, SellingCostProfile
from app.money import to_major
from app.schemas.common import ApiModel


class GradingTierOut(ApiModel):
    id: str
    company_id: str
    tier_code: str
    tier_name: str
    price: float
    currency: str
    minimum_cards: int
    maximum_cards: int | None
    min_declared_value: float | None
    max_declared_value: float | None
    turnaround_days: int | None
    membership_required: bool
    membership_discount_pct: float
    additional_fees: float
    per_card_fees: float
    declared_value_fee_pct: float
    effective_from: date | None
    effective_to: date | None
    active: bool
    sort_order: int
    source_url: str | None
    source_checked_at: date | None
    notes: str | None

    @classmethod
    def from_model(cls, tier: GradingTier) -> GradingTierOut:
        return cls(
            id=tier.id,
            company_id=tier.company_id,
            tier_code=tier.tier_code,
            tier_name=tier.tier_name,
            price=to_major(tier.price_minor) or 0.0,
            currency=tier.currency,
            minimum_cards=tier.minimum_cards,
            maximum_cards=tier.maximum_cards,
            min_declared_value=to_major(tier.min_declared_value_minor),
            max_declared_value=to_major(tier.max_declared_value_minor),
            turnaround_days=tier.turnaround_days,
            membership_required=tier.membership_required,
            membership_discount_pct=tier.membership_discount_pct,
            additional_fees=to_major(tier.additional_fees_minor) or 0.0,
            per_card_fees=to_major(tier.per_card_fees_minor) or 0.0,
            declared_value_fee_pct=tier.declared_value_fee_pct,
            effective_from=tier.effective_from,
            effective_to=tier.effective_to,
            active=tier.active,
            sort_order=tier.sort_order,
            source_url=tier.source_url,
            source_checked_at=tier.source_checked_at,
            notes=tier.notes,
        )


class GradingTierWrite(ApiModel):
    tier_code: str = Field(min_length=1, max_length=48)
    tier_name: str = Field(min_length=1, max_length=80)
    price: float = Field(ge=0)
    currency: str = "GBP"
    minimum_cards: int = Field(default=1, ge=1)
    maximum_cards: int | None = Field(default=None, ge=1)
    min_declared_value: float | None = Field(default=None, ge=0)
    max_declared_value: float | None = Field(default=None, ge=0)
    turnaround_days: int | None = Field(default=None, ge=0)
    membership_required: bool = False
    membership_discount_pct: float = Field(default=0.0, ge=0, le=100)
    additional_fees: float = Field(default=0.0, ge=0)
    per_card_fees: float = Field(default=0.0, ge=0)
    declared_value_fee_pct: float = Field(default=0.0, ge=0, le=100)
    effective_from: date | None = None
    effective_to: date | None = None
    active: bool = True
    sort_order: int = 100
    source_url: str | None = None
    source_checked_at: date | None = None
    notes: str | None = None


class GradingMembershipOut(ApiModel):
    id: str
    company_id: str
    code: str
    name: str
    annual_fee: float
    currency: str
    included_credits: int
    discount_pct: float
    user_holds: bool
    expires_on: date | None
    active: bool
    source_url: str | None
    notes: str | None

    @classmethod
    def from_model(cls, membership: GradingMembership) -> GradingMembershipOut:
        return cls(
            id=membership.id,
            company_id=membership.company_id,
            code=membership.code,
            name=membership.name,
            annual_fee=to_major(membership.annual_fee_minor) or 0.0,
            currency=membership.currency,
            included_credits=membership.included_credits,
            discount_pct=membership.discount_pct,
            user_holds=membership.user_holds,
            expires_on=membership.expires_on,
            active=membership.active,
            source_url=membership.source_url,
            notes=membership.notes,
        )


class GradingMembershipWrite(ApiModel):
    code: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=120)
    annual_fee: float = Field(default=0.0, ge=0)
    currency: str = "GBP"
    included_credits: int = Field(default=0, ge=0)
    discount_pct: float = Field(default=0.0, ge=0, le=100)
    user_holds: bool = False
    expires_on: date | None = None
    active: bool = True
    source_url: str | None = None
    notes: str | None = None


class GradingCompanyOut(ApiModel):
    id: str
    code: str
    name: str
    country: str | None
    currency: str
    website: str | None
    market_recognition_score: float
    grade_scale_max: float
    supports_half_grades: bool
    supports_subgrades: bool
    active: bool
    sort_order: int
    notes: str | None
    tiers: list[GradingTierOut] = Field(default_factory=list)
    memberships: list[GradingMembershipOut] = Field(default_factory=list)

    @classmethod
    def from_model(cls, company: GradingCompany, include_children: bool = True) -> GradingCompanyOut:
        return cls(
            id=company.id,
            code=company.code,
            name=company.name,
            country=company.country,
            currency=company.currency,
            website=company.website,
            market_recognition_score=company.market_recognition_score,
            grade_scale_max=company.grade_scale_max,
            supports_half_grades=company.supports_half_grades,
            supports_subgrades=company.supports_subgrades,
            active=company.active,
            sort_order=company.sort_order,
            notes=company.notes,
            tiers=(
                sorted(
                    (GradingTierOut.from_model(t) for t in company.tiers),
                    key=lambda t: (t.sort_order, t.price),
                )
                if include_children
                else []
            ),
            memberships=(
                [GradingMembershipOut.from_model(m) for m in company.memberships]
                if include_children
                else []
            ),
        )


class GradingCompanyWrite(ApiModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    country: str | None = None
    currency: str = "GBP"
    website: str | None = None
    market_recognition_score: float = Field(default=5.0, ge=0, le=10)
    grade_scale_max: float = Field(default=10.0, gt=0)
    supports_half_grades: bool = False
    supports_subgrades: bool = False
    active: bool = True
    sort_order: int = 100
    notes: str | None = None


class SellingProfileOut(ApiModel):
    id: str
    code: str
    name: str
    platform: str | None
    currency: str
    platform_fee_pct: float
    payment_fee_pct: float
    payment_fixed_fee: float
    listing_fee: float
    other_fee_pct: float
    fees_apply_to_shipping: bool
    shipping_charged_to_buyer: float
    shipping_cost: float
    packaging_cost: float
    graded_shipping_cost: float | None
    graded_packaging_cost: float | None
    is_default: bool
    active: bool
    sort_order: int
    notes: str | None

    @classmethod
    def from_model(cls, profile: SellingCostProfile) -> SellingProfileOut:
        return cls(
            id=profile.id,
            code=profile.code,
            name=profile.name,
            platform=profile.platform,
            currency=profile.currency,
            platform_fee_pct=profile.platform_fee_pct,
            payment_fee_pct=profile.payment_fee_pct,
            payment_fixed_fee=to_major(profile.payment_fixed_fee_minor) or 0.0,
            listing_fee=to_major(profile.listing_fee_minor) or 0.0,
            other_fee_pct=profile.other_fee_pct,
            fees_apply_to_shipping=profile.fees_apply_to_shipping,
            shipping_charged_to_buyer=to_major(profile.shipping_charged_to_buyer_minor) or 0.0,
            shipping_cost=to_major(profile.shipping_cost_minor) or 0.0,
            packaging_cost=to_major(profile.packaging_cost_minor) or 0.0,
            graded_shipping_cost=to_major(profile.graded_shipping_cost_minor),
            graded_packaging_cost=to_major(profile.graded_packaging_cost_minor),
            is_default=profile.is_default,
            active=profile.active,
            sort_order=profile.sort_order,
            notes=profile.notes,
        )


class SellingProfileWrite(ApiModel):
    code: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=120)
    platform: str | None = None
    currency: str = "GBP"
    platform_fee_pct: float = Field(default=0.0, ge=0, le=100)
    payment_fee_pct: float = Field(default=0.0, ge=0, le=100)
    payment_fixed_fee: float = Field(default=0.0, ge=0)
    listing_fee: float = Field(default=0.0, ge=0)
    other_fee_pct: float = Field(default=0.0, ge=0, le=100)
    fees_apply_to_shipping: bool = True
    shipping_charged_to_buyer: float = Field(default=0.0, ge=0)
    shipping_cost: float = Field(default=0.0, ge=0)
    packaging_cost: float = Field(default=0.0, ge=0)
    graded_shipping_cost: float | None = Field(default=None, ge=0)
    graded_packaging_cost: float | None = Field(default=None, ge=0)
    is_default: bool = False
    active: bool = True
    sort_order: int = 100
    notes: str | None = None


class DataSourceOut(ApiModel):
    id: str
    code: str
    name: str
    kind: str
    base_url: str | None
    api_key_env_var: str | None
    enabled: bool
    priority: int
    has_adapter: bool
    api_key_present: bool
    last_sync_at: str | None = None
    last_sync_status: str | None
    terms_url: str | None
    notes: str | None
