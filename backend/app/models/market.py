"""Market data: sources, sales, listings, computed prices and daily snapshots.

The local database is the source of truth (spec, closing section). Providers
write *into* these tables; every calculation downstream reads only from here, so
an API going dark costs the user future updates, never their history.

Rows are keyed by ``catalog_key`` — a normalised card identity — rather than by
``card_id``, so two physical copies of the same card share one market history.
``card_id`` is kept as an optional convenience link for manually entered rows.
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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import Confidence, DataSourceKind, SaleExclusionReason
from app.models.base import Base, TimestampMixin, enum_check, money_column, pk_column, utcnow


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"
    __table_args__ = (enum_check("kind", DataSourceKind),)

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_class: Mapped[str | None] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(String(255))
    # Credentials live in the OS environment; this holds only non-secret config
    # plus the *name* of the env var holding the key.
    api_key_env_var: Mapped[str | None] = mapped_column(String(80))
    config: Mapped[dict | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(32))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)


class MarketSale(Base):
    """A single completed sale. The primary evidence for every valuation."""

    __tablename__ = "market_sales"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_sale_source_external"),
        Index("ix_market_sales_key_grade_date", "catalog_key", "grade_label", "sale_date"),
        CheckConstraint(
            "exclusion_reason IS NULL OR exclusion_reason IN ("
            + ", ".join(f"'{value}'" for value in SaleExclusionReason.values())
            + ")",
            name="exclusion_reason_valid",
        ),
    )

    id: Mapped[str] = pk_column()
    catalog_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="SET NULL")
    )
    grade: Mapped[float | None] = mapped_column(Float)  # NULL = raw
    grade_label: Mapped[str] = mapped_column(String(24), nullable=False, default="raw", index=True)

    platform: Mapped[str | None] = mapped_column(String(48))
    sale_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    sale_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    shipping_minor: Mapped[int | None] = money_column()
    condition_note: Mapped[str | None] = mapped_column(String(120))
    listing_title: Mapped[str | None] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(512))
    seller: Mapped[str | None] = mapped_column(String(120))
    bid_count: Mapped[int | None] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_auction: Mapped[bool | None] = mapped_column(Boolean)

    # Filtering (spec section 15). Excluded rows are kept, never deleted, so the
    # user can inspect and reverse any automatic exclusion.
    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(32))
    excluded_by: Mapped[str | None] = mapped_column(String(16))  # system | user
    is_outlier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    source_id: Mapped[str | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(160))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class MarketListing(Base):
    """An active (unsold) listing. Feeds the sold-to-active ratio in §17."""

    __tablename__ = "market_listings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        Index("ix_market_listings_key_grade", "catalog_key", "grade_label"),
    )

    id: Mapped[str] = pk_column()
    catalog_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="SET NULL")
    )
    grade: Mapped[float | None] = mapped_column(Float)
    grade_label: Mapped[str] = mapped_column(String(24), nullable=False, default="raw")
    platform: Mapped[str | None] = mapped_column(String(48))
    listed_at: Mapped[date | None] = mapped_column(Date)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    shipping_minor: Mapped[int | None] = money_column()
    listing_title: Mapped[str | None] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(512))
    seller: Mapped[str | None] = mapped_column(String(120))
    is_auction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(160))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class MarketPrice(Base, TimestampMixin):
    """Derived valuation for one (identity, grade) pair — spec section 16.

    Not a raw feed: every column here is computed from ``market_sales`` by the
    pricing service, which is why the sample size, window and confidence travel
    with the number. A price without its evidence is false precision (§36).
    """

    __tablename__ = "market_prices"
    __table_args__ = (
        UniqueConstraint("catalog_key", "grade_label", "source_id", name="uq_price_key_grade_source"),
        enum_check("confidence", Confidence),
    )

    id: Mapped[str] = pk_column()
    catalog_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="SET NULL")
    )
    grade: Mapped[float | None] = mapped_column(Float)
    grade_label: Mapped[str] = mapped_column(String(24), nullable=False, default="raw")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    median_minor: Mapped[int | None] = money_column()
    weighted_median_minor: Mapped[int | None] = money_column()
    mean_minor: Mapped[int | None] = money_column()
    low_quartile_minor: Mapped[int | None] = money_column()
    high_quartile_minor: Mapped[int | None] = money_column()
    last_sale_minor: Mapped[int | None] = money_column()
    realistic_sale_minor: Mapped[int | None] = money_column()
    quick_sale_minor: Mapped[int | None] = money_column()

    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Yearly units sold, when a source reports it. A *count*, not evidence of
    #: what anything sold for — it feeds liquidity and nothing else, and it is
    #: deliberately not turned into rows in ``market_sales``, where an invented
    #: date would corrupt trend, valuation and the outlier fence at once.
    #:
    #: Describes the whole product, pooled across grades, which is the shape
    #: liquidity is already measured at.
    annual_volume: Mapped[int | None] = mapped_column(Integer)
    window_days: Mapped[int | None] = mapped_column(Integer)
    last_sale_at: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default=Confidence.NONE.value)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Spec section 35: never overwrite the system's number with the user's.
    user_value_minor: Mapped[int | None] = money_column()
    user_value_note: Mapped[str | None] = mapped_column(Text)

    source_id: Mapped[str | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))


class PriceSnapshot(Base):
    """One row per identity/grade/day — the user's own price history (§38)."""

    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "catalog_key", "grade_label", "snapshot_date", "source_id", name="uq_snapshot_unique"
        ),
        Index("ix_price_snapshots_key_date", "catalog_key", "snapshot_date"),
    )

    id: Mapped[str] = pk_column()
    catalog_key: Mapped[str] = mapped_column(String(200), nullable=False)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("grading_companies.id", ondelete="SET NULL")
    )
    grade: Mapped[float | None] = mapped_column(Float)
    grade_label: Mapped[str] = mapped_column(String(24), nullable=False, default="raw")
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    value_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_listings: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
