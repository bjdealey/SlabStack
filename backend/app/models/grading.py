"""Grading companies, price tiers, memberships and submissions.

Nothing about PSA, CGC or ACE is hard-coded anywhere in the engine — a grading
company is a row, a price tier is a row, and both carry ``effective_from`` /
``effective_to`` so that historical submissions keep costing what they actually
cost when grader pricing changes (spec section 10).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import (
    CostAllocationMethod,
    DeclaredValueSource,
    SubmissionCardStatus,
    SubmissionStatus,
)
from app.models.base import Base, TimestampMixin, enum_check, money_column, pk_column, utcnow


class GradingCompany(Base, TimestampMixin):
    __tablename__ = "grading_companies"

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    website: Mapped[str | None] = mapped_column(String(255))
    # 0-10. How readily the market accepts this slab; feeds the liquidity-aware
    # tie-break in spec section 26. A default, not a fact — user-editable.
    market_recognition_score: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    # Grade points this company is assumed to award above (+) or below (-) the
    # model's baseline. Ships at 0.0 for every company on purpose: we make no
    # claim about who grades harder. The user tunes it from their own returned
    # submissions, and Phase 8 can calibrate it from prediction_results.
    strictness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    grade_scale_max: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    supports_half_grades: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_subgrades: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    notes: Mapped[str | None] = mapped_column(Text)

    tiers: Mapped[list[GradingTier]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    memberships: Mapped[list[GradingMembership]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )


class GradingTier(Base, TimestampMixin):
    __tablename__ = "grading_tiers"
    __table_args__ = (
        UniqueConstraint("company_id", "tier_code", "effective_from", name="uq_tier_effective"),
        CheckConstraint("minimum_cards >= 1", name="minimum_cards_positive"),
    )

    id: Mapped[str] = pk_column()
    company_id: Mapped[str] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tier_code: Mapped[str] = mapped_column(String(48), nullable=False)
    tier_name: Mapped[str] = mapped_column(String(80), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    minimum_cards: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    maximum_cards: Mapped[int | None] = mapped_column(Integer)
    min_declared_value_minor: Mapped[int | None] = money_column()
    max_declared_value_minor: Mapped[int | None] = money_column()
    turnaround_days: Mapped[int | None] = mapped_column(Integer)

    membership_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    membership_discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Flat per-submission additions (e.g. handling) and per-card additions
    # (e.g. sleeving, insurance uplift) kept apart because they allocate
    # differently across a batch.
    additional_fees_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_card_fees_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Percentage of declared value charged as insurance/fee by some graders.
    declared_value_fee_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    source_url: Mapped[str | None] = mapped_column(String(255))
    source_checked_at: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    company: Mapped[GradingCompany] = relationship(back_populates="tiers")


class GradingMembership(Base, TimestampMixin):
    """Annual memberships, needed to answer "is membership worthwhile?" (§11)."""

    __tablename__ = "grading_memberships"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_membership_code"),)

    id: Mapped[str] = pk_column()
    company_id: Mapped[str] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    annual_fee_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    included_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    user_holds: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_url: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    company: Mapped[GradingCompany] = relationship(back_populates="memberships")


class GradingSubmission(Base, TimestampMixin):
    __tablename__ = "grading_submissions"
    __table_args__ = (
        enum_check("status", SubmissionStatus),
        enum_check("cost_allocation_method", CostAllocationMethod),
    )

    id: Mapped[str] = pk_column()
    reference: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(120))
    company_id: Mapped[str] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tier_id: Mapped[str | None] = mapped_column(ForeignKey("grading_tiers.id", ondelete="SET NULL"))
    membership_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_memberships.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SubmissionStatus.DRAFT.value, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    # Shared costs, allocated across cards (spec section 12).
    shipping_out_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipping_return_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    insurance_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    membership_allocation_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    handling_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    other_fees_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_allocation_method: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CostAllocationMethod.EQUAL.value
    )

    submitted_at: Mapped[date | None] = mapped_column(Date)
    received_at: Mapped[date | None] = mapped_column(Date)
    returned_at: Mapped[date | None] = mapped_column(Date)
    tracking_outbound: Mapped[str | None] = mapped_column(String(120))
    tracking_return: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)

    cards: Mapped[list[SubmissionCard]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", lazy="selectin"
    )


class SubmissionCard(Base):
    __tablename__ = "submission_cards"
    __table_args__ = (
        UniqueConstraint("submission_id", "card_id", name="uq_submission_card"),
        enum_check("status", SubmissionCardStatus),
        enum_check("declared_value_source", DeclaredValueSource),
    )

    id: Mapped[str] = pk_column()
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("grading_submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tier_id: Mapped[str | None] = mapped_column(ForeignKey("grading_tiers.id", ondelete="SET NULL"))

    # Spec section 13: the system's suggestion and the user's number stay apart.
    declared_value_minor: Mapped[int | None] = money_column()
    system_declared_value_minor: Mapped[int | None] = money_column()
    declared_value_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeclaredValueSource.SYSTEM.value
    )
    declared_value_confidence: Mapped[str | None] = mapped_column(String(16))

    grading_fee_minor: Mapped[int | None] = money_column()
    allocated_overhead_minor: Mapped[int | None] = money_column()

    predicted_grade: Mapped[float | None] = mapped_column(Float)
    actual_grade: Mapped[float | None] = mapped_column(Float)
    cert_number: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SubmissionCardStatus.PLANNED.value
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    submission: Mapped[GradingSubmission] = relationship(back_populates="cards")
