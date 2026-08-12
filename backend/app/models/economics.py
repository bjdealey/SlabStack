"""Selling cost profiles and application settings.

Both are configuration: the decision engine reads fees and thresholds from here
so that "what would this actually net me?" reflects the user's own platforms and
risk appetite, not a hard-coded assumption (spec sections 22, 41, 42).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, money_column, pk_column


class SellingCostProfile(Base, TimestampMixin):
    __tablename__ = "selling_cost_profiles"

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(48))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    platform_fee_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payment_fee_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payment_fixed_fee_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    listing_fee_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    other_fee_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Whether the platform charges its percentage on the postage the buyer pays
    # — small per card, material across a 400-card collection.
    fees_apply_to_shipping: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    shipping_charged_to_buyer_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipping_cost_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packaging_cost_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Graded slabs post heavier and are usually sent tracked/insured.
    graded_shipping_cost_minor: Mapped[int | None] = money_column()
    graded_packaging_cost_minor: Mapped[int | None] = money_column()

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    notes: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base, TimestampMixin):
    """Typed key/value store.

    Only user-modified keys are persisted; defaults live in
    ``app.services.settings.SETTING_DEFINITIONS`` so that a new setting appears
    with a sensible value without a data migration.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class CardDisposal(Base, TimestampMixin):
    """What a card actually fetched — the one figure the engine never estimates.

    Everything else in this application is a projection: what a card is worth,
    what grading would cost, what a sale would net. This is the row that closes
    the loop, and it exists because the app could previously learn whether its
    *grade* predictions were right and never whether its *profit* predictions
    were — which is a strange gap in a build whose stated purpose is realisable
    profit rather than theoretical value.

    ``catalog_key`` is denormalised for the same reason ``market_sales`` does it:
    deleting a card should lose the card, not the lesson.
    """

    __tablename__ = "card_disposals"

    id: Mapped[str] = pk_column()
    card_id: Mapped[str | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL"), index=True
    )
    catalog_key: Mapped[str | None] = mapped_column(String(200), index=True)
    #: Kept so a sold card still reads as something after the card row is gone.
    card_name: Mapped[str | None] = mapped_column(String(160))

    sold_on: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str | None] = mapped_column(String(48))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    # --- What was sold ------------------------------------------------------
    # Raw or slabbed decides which decision is being scored, so it is stored
    # rather than inferred from whether a grade happens to be filled in.
    sold_graded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="SET NULL")
    )
    grade: Mapped[float | None] = mapped_column(Float)
    grade_label: Mapped[str] = mapped_column(String(24), nullable=False, default="raw")

    # --- The money ----------------------------------------------------------
    gross_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_income_minor: Mapped[int | None] = money_column()
    #: Everything the platform and payment processor took, as one figure. A
    #: payout statement gives a total far more readily than a breakdown.
    fees_minor: Mapped[int | None] = money_column()
    postage_cost_minor: Mapped[int | None] = money_column()
    packaging_cost_minor: Mapped[int | None] = money_column()
    net_proceeds_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    #: True when the user typed the payout rather than letting it be derived.
    #: Kept apart from the derived figure for the same reason every other
    #: override in this schema is: the two answer different questions.
    net_is_user_entered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- What it cost to get here -------------------------------------------
    #: What grading actually cost, when it was graded. Null means unrecorded,
    #: not free — a realised profit computed without it would flatter grading.
    grading_cost_minor: Mapped[int | None] = money_column()

    notes: Mapped[str | None] = mapped_column(Text)
