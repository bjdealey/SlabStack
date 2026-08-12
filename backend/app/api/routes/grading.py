"""Grading companies, tiers, memberships, selling profiles and data sources.

All of this is configuration the user owns. The decision engine reads it; it
never hard-codes any of it.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.api.deps import CompanyDep, DbSession
from app.api.errors import ConflictError, NotFoundError
from app.models import (
    DataSource,
    GradeRule,
    GradingCompany,
    GradingMembership,
    GradingSubmission,
    GradingTier,
    SellingCostProfile,
)
from app.money import to_minor
from app.schemas.grading import (
    CredentialOut,
    DataSourceOut,
    GradeRuleOut,
    GradeRuleWrite,
    GradingCompanyOut,
    GradingCompanyWrite,
    GradingMembershipOut,
    GradingMembershipWrite,
    GradingTierOut,
    GradingTierWrite,
    SellingProfileOut,
    SellingProfileWrite,
)
from app.services.market_data.registry import credentials_present

router = APIRouter(tags=["configuration"])

# API field -> column, for the money fields on each writable model.
_TIER_MONEY = {
    "price": "price_minor",
    "min_declared_value": "min_declared_value_minor",
    "max_declared_value": "max_declared_value_minor",
    "additional_fees": "additional_fees_minor",
    "per_card_fees": "per_card_fees_minor",
}
_PROFILE_MONEY = {
    "payment_fixed_fee": "payment_fixed_fee_minor",
    "listing_fee": "listing_fee_minor",
    "shipping_charged_to_buyer": "shipping_charged_to_buyer_minor",
    "shipping_cost": "shipping_cost_minor",
    "packaging_cost": "packaging_cost_minor",
    "graded_shipping_cost": "graded_shipping_cost_minor",
    "graded_packaging_cost": "graded_packaging_cost_minor",
}


def _apply(target, payload: dict, money_map: dict[str, str]) -> None:
    for field, value in payload.items():
        if field in money_map:
            setattr(target, money_map[field], to_minor(value))
        else:
            setattr(target, field, value)


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------


@router.get("/grading/companies", response_model=list[GradingCompanyOut], summary="List grading companies")
def list_companies(db: DbSession, include_inactive: bool = True) -> list[GradingCompanyOut]:
    stmt = select(GradingCompany).order_by(GradingCompany.sort_order, GradingCompany.code)
    if not include_inactive:
        stmt = stmt.where(GradingCompany.active.is_(True))
    return [GradingCompanyOut.from_model(company) for company in db.scalars(stmt)]


@router.post(
    "/grading/companies",
    response_model=GradingCompanyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a grading company",
)
def create_company(db: DbSession, payload: GradingCompanyWrite) -> GradingCompanyOut:
    if db.scalars(select(GradingCompany).where(GradingCompany.code == payload.code)).first():
        raise ConflictError(f"Grading company '{payload.code}' already exists.")
    company = GradingCompany(**payload.model_dump())
    db.add(company)
    db.flush()
    return GradingCompanyOut.from_model(company)


@router.patch(
    "/grading/companies/{company_id}",
    response_model=GradingCompanyOut,
    summary="Update a grading company",
)
def update_company(db: DbSession, company: CompanyDep, payload: GradingCompanyWrite) -> GradingCompanyOut:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    db.flush()
    return GradingCompanyOut.from_model(company)


@router.delete(
    "/grading/companies/{company_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a grading company",
)
def delete_company(db: DbSession, company: CompanyDep) -> Response:
    in_use = (
        db.scalar(
            select(func.count())
            .select_from(GradingSubmission)
            .where(GradingSubmission.company_id == company.id)
        )
        or 0
    )
    if in_use:
        raise ConflictError(
            f"{in_use} submission(s) reference {company.code}. Deactivate it instead.",
            {"submissions": in_use},
        )
    db.delete(company)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


@router.get(
    "/grading/companies/{company_id}/tiers",
    response_model=list[GradingTierOut],
    summary="List a company's price tiers",
)
def list_tiers(company: CompanyDep) -> list[GradingTierOut]:
    return [
        GradingTierOut.from_model(tier)
        for tier in sorted(company.tiers, key=lambda t: (t.sort_order, t.price_minor))
    ]


@router.post(
    "/grading/companies/{company_id}/tiers",
    response_model=GradingTierOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a price tier",
)
def create_tier(db: DbSession, company: CompanyDep, payload: GradingTierWrite) -> GradingTierOut:
    tier = GradingTier(company_id=company.id)
    _apply(tier, payload.model_dump(), _TIER_MONEY)
    db.add(tier)
    db.flush()
    return GradingTierOut.from_model(tier)


@router.patch("/grading/tiers/{tier_id}", response_model=GradingTierOut, summary="Update a price tier")
def update_tier(db: DbSession, tier_id: str, payload: GradingTierWrite) -> GradingTierOut:
    tier = db.get(GradingTier, tier_id)
    if tier is None:
        raise NotFoundError("Grading tier", tier_id)
    _apply(tier, payload.model_dump(exclude_unset=True), _TIER_MONEY)
    db.flush()
    return GradingTierOut.from_model(tier)


@router.delete(
    "/grading/tiers/{tier_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a price tier"
)
def delete_tier(db: DbSession, tier_id: str) -> Response:
    tier = db.get(GradingTier, tier_id)
    if tier is None:
        raise NotFoundError("Grading tier", tier_id)
    db.delete(tier)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------


@router.get(
    "/grading/companies/{company_id}/memberships",
    response_model=list[GradingMembershipOut],
    summary="List a company's memberships",
)
def list_memberships(company: CompanyDep) -> list[GradingMembershipOut]:
    return [GradingMembershipOut.from_model(m) for m in company.memberships]


@router.post(
    "/grading/companies/{company_id}/memberships",
    response_model=GradingMembershipOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a membership",
)
def create_membership(
    db: DbSession, company: CompanyDep, payload: GradingMembershipWrite
) -> GradingMembershipOut:
    membership = GradingMembership(company_id=company.id)
    _apply(membership, payload.model_dump(), {"annual_fee": "annual_fee_minor"})
    db.add(membership)
    db.flush()
    return GradingMembershipOut.from_model(membership)


@router.patch(
    "/grading/memberships/{membership_id}",
    response_model=GradingMembershipOut,
    summary="Update a membership",
)
def update_membership(
    db: DbSession, membership_id: str, payload: GradingMembershipWrite
) -> GradingMembershipOut:
    membership = db.get(GradingMembership, membership_id)
    if membership is None:
        raise NotFoundError("Membership", membership_id)
    _apply(membership, payload.model_dump(exclude_unset=True), {"annual_fee": "annual_fee_minor"})
    db.flush()
    return GradingMembershipOut.from_model(membership)


@router.delete(
    "/grading/memberships/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a membership",
)
def delete_membership(db: DbSession, membership_id: str) -> Response:
    membership = db.get(GradingMembership, membership_id)
    if membership is None:
        raise NotFoundError("Membership", membership_id)
    db.delete(membership)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Selling cost profiles
# ---------------------------------------------------------------------------


@router.get("/selling-profiles", response_model=list[SellingProfileOut], summary="List selling profiles")
def list_profiles(db: DbSession, include_inactive: bool = True) -> list[SellingProfileOut]:
    stmt = select(SellingCostProfile).order_by(SellingCostProfile.sort_order, SellingCostProfile.name)
    if not include_inactive:
        stmt = stmt.where(SellingCostProfile.active.is_(True))
    return [SellingProfileOut.from_model(profile) for profile in db.scalars(stmt)]


@router.post(
    "/selling-profiles",
    response_model=SellingProfileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a selling profile",
)
def create_profile(db: DbSession, payload: SellingProfileWrite) -> SellingProfileOut:
    if db.scalars(select(SellingCostProfile).where(SellingCostProfile.code == payload.code)).first():
        raise ConflictError(f"Selling profile '{payload.code}' already exists.")
    profile = SellingCostProfile()
    _apply(profile, payload.model_dump(), _PROFILE_MONEY)
    db.add(profile)
    db.flush()
    _enforce_single_default(db, profile)
    return SellingProfileOut.from_model(profile)


@router.patch(
    "/selling-profiles/{profile_id}", response_model=SellingProfileOut, summary="Update a selling profile"
)
def update_profile(db: DbSession, profile_id: str, payload: SellingProfileWrite) -> SellingProfileOut:
    profile = db.get(SellingCostProfile, profile_id)
    if profile is None:
        raise NotFoundError("Selling profile", profile_id)
    _apply(profile, payload.model_dump(exclude_unset=True), _PROFILE_MONEY)
    db.flush()
    _enforce_single_default(db, profile)
    return SellingProfileOut.from_model(profile)


@router.delete(
    "/selling-profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a selling profile",
)
def delete_profile(db: DbSession, profile_id: str) -> Response:
    profile = db.get(SellingCostProfile, profile_id)
    if profile is None:
        raise NotFoundError("Selling profile", profile_id)
    db.delete(profile)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _enforce_single_default(db: DbSession, profile: SellingCostProfile) -> None:
    if not profile.is_default:
        return
    for other in db.scalars(select(SellingCostProfile).where(SellingCostProfile.id != profile.id)):
        other.is_default = False
    db.flush()


# ---------------------------------------------------------------------------
# Grade rules
# ---------------------------------------------------------------------------


def _rule_out(rule: GradeRule, codes: dict[str, str]) -> GradeRuleOut:
    out = GradeRuleOut.model_validate(rule)
    out.company_code = codes.get(rule.company_id or "")
    return out


@router.get(
    "/grading/rules",
    response_model=list[GradeRuleOut],
    summary="List grade rules",
    description=(
        "Defect caps and probability adjustments used by the grade model. These are "
        "SlabStack's estimates, not any grader's published standard — edit them freely."
    ),
)
def list_rules(db: DbSession, include_inactive: bool = True) -> list[GradeRuleOut]:
    stmt = select(GradeRule).order_by(GradeRule.sort_order, GradeRule.code)
    if not include_inactive:
        stmt = stmt.where(GradeRule.active.is_(True))
    codes = {company.id: company.code for company in db.scalars(select(GradingCompany))}
    return [_rule_out(rule, codes) for rule in db.scalars(stmt)]


@router.post(
    "/grading/rules",
    response_model=GradeRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a grade rule",
)
def create_rule(db: DbSession, payload: GradeRuleWrite) -> GradeRuleOut:
    if db.scalars(select(GradeRule).where(GradeRule.code == payload.code)).first():
        raise ConflictError(f"A rule with code '{payload.code}' already exists.")
    rule = GradeRule(**payload.model_dump())
    db.add(rule)
    db.flush()
    codes = {company.id: company.code for company in db.scalars(select(GradingCompany))}
    return _rule_out(rule, codes)


@router.patch("/grading/rules/{rule_id}", response_model=GradeRuleOut, summary="Update a grade rule")
def update_rule(db: DbSession, rule_id: str, payload: GradeRuleWrite) -> GradeRuleOut:
    rule = db.get(GradeRule, rule_id)
    if rule is None:
        raise NotFoundError("Grade rule", rule_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    db.flush()
    codes = {company.id: company.code for company in db.scalars(select(GradingCompany))}
    return _rule_out(rule, codes)


@router.delete(
    "/grading/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a grade rule",
)
def delete_rule(db: DbSession, rule_id: str) -> Response:
    rule = db.get(GradeRule, rule_id)
    if rule is None:
        raise NotFoundError("Grade rule", rule_id)
    if rule.is_builtin:
        raise ConflictError(
            "Built-in rules cannot be deleted — deactivate it instead, so it can be restored."
        )
    db.delete(rule)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------


@router.get("/data-sources", response_model=list[DataSourceOut], summary="List market data sources")
def list_data_sources(db: DbSession) -> list[DataSourceOut]:
    rows = db.scalars(select(DataSource).order_by(DataSource.priority, DataSource.name))
    result = []
    for source in rows:
        result.append(
            DataSourceOut(
                id=source.id,
                code=source.code,
                name=source.name,
                kind=source.kind,
                base_url=source.base_url,
                api_key_env_var=source.api_key_env_var,
                enabled=source.enabled,
                priority=source.priority,
                has_adapter=bool(source.provider_class),
                # Report only whether a key is present, never the value.
                api_key_present=bool(
                    source.api_key_env_var and os.environ.get(source.api_key_env_var)
                ),
                credentials=[
                    CredentialOut(env_var=name, present=present)
                    for name, present in credentials_present(source).items()
                ],
                last_sync_at=source.last_sync_at.isoformat() if source.last_sync_at else None,
                last_sync_status=source.last_sync_status,
                terms_url=source.terms_url,
                notes=source.notes,
            )
        )
    return result
