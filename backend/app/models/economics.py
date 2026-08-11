"""Selling cost profiles and application settings.

Both are configuration: the decision engine reads fees and thresholds from here
so that "what would this actually net me?" reflects the user's own platforms and
risk appetite, not a hard-coded assumption (spec sections 22, 41, 42).
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
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
