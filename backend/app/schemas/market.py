"""Request and response shapes for sales, listings and computed prices.

The API speaks major units (``18.80``); the database stores minor units
(``1880``). The conversion happens here and nowhere else, so no route does
arithmetic on a float.

Every derived figure that leaves this module carries the evidence behind it —
``sample_size``, ``window_days``, ``last_sale_at``, ``confidence`` — because a
number without them invites the reader to trust it more than it deserves.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, field_validator

from app.enums import Confidence, LiquidityBand, SaleExclusionReason, TrendDirection
from app.models import MarketListing, MarketPrice, MarketSale
from app.money import to_major, to_minor
from app.schemas.common import ApiModel


class SaleBase(ApiModel):
    sale_date: date
    sale_price: float = Field(gt=0, description="Price paid, in major units.")
    currency: str | None = None
    shipping: float | None = Field(default=None, ge=0)
    grade_label: str | None = Field(
        default=None,
        description="`raw`, or a slab label such as `PSA 10`. Derived from company + grade if omitted.",
    )
    company_id: str | None = None
    grade: float | None = Field(default=None, ge=0, le=10)
    platform: str | None = None
    listing_title: str | None = None
    source_url: str | None = None
    seller: str | None = None
    bid_count: int | None = Field(default=None, ge=0)
    lot_size: int = Field(default=1, ge=1)
    is_auction: bool | None = None
    condition_note: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class SaleCreate(SaleBase):
    """A sale entered by hand or posted one at a time."""

    external_id: str | None = None
    apply_filters: bool = Field(
        default=True,
        description=(
            "Run the lot/damage/language/variant heuristics. A sale you typed in yourself is "
            "usually one you already checked, so set false to store it exactly as given."
        ),
    )


class SaleUpdate(ApiModel):
    sale_date: date | None = None
    sale_price: float | None = Field(default=None, gt=0)
    shipping: float | None = Field(default=None, ge=0)
    grade_label: str | None = None
    company_id: str | None = None
    grade: float | None = Field(default=None, ge=0, le=10)
    platform: str | None = None
    listing_title: str | None = None
    source_url: str | None = None
    seller: str | None = None
    lot_size: int | None = Field(default=None, ge=1)
    is_auction: bool | None = None
    condition_note: str | None = None


class SaleExclusionWrite(ApiModel):
    """Include or exclude a sale by hand. Overrides the system's verdict for good."""

    excluded: bool
    reason: str | None = Field(
        default=None,
        description=f"One of: {', '.join(SaleExclusionReason.values())}. Defaults to user_excluded.",
    )


class SaleOut(ApiModel):
    id: str
    catalog_key: str
    card_id: str | None = None
    company_id: str | None = None
    grade: float | None = None
    grade_label: str
    platform: str | None = None
    sale_date: date
    sale_price: float
    currency: str
    shipping: float | None = None
    total_paid: float | None = None
    condition_note: str | None = None
    listing_title: str | None = None
    source_url: str | None = None
    seller: str | None = None
    bid_count: int | None = None
    lot_size: int = 1
    is_auction: bool | None = None
    is_excluded: bool = False
    exclusion_reason: str | None = None
    excluded_by: str | None = None
    is_outlier: bool = False
    source_id: str | None = None
    external_id: str | None = None
    imported_at: datetime | None = None

    @classmethod
    def from_model(cls, row: MarketSale) -> SaleOut:
        price = to_major(row.sale_price_minor)
        shipping = to_major(row.shipping_minor)
        return cls(
            id=row.id,
            catalog_key=row.catalog_key,
            card_id=row.card_id,
            company_id=row.company_id,
            grade=row.grade,
            grade_label=row.grade_label,
            platform=row.platform,
            sale_date=row.sale_date,
            sale_price=price if price is not None else 0.0,
            currency=row.currency,
            shipping=shipping,
            total_paid=to_major(row.sale_price_minor + (row.shipping_minor or 0)),
            condition_note=row.condition_note,
            listing_title=row.listing_title,
            source_url=row.source_url,
            seller=row.seller,
            bid_count=row.bid_count,
            lot_size=row.lot_size,
            is_auction=row.is_auction,
            is_excluded=row.is_excluded,
            exclusion_reason=row.exclusion_reason,
            excluded_by=row.excluded_by,
            is_outlier=row.is_outlier,
            source_id=row.source_id,
            external_id=row.external_id,
            imported_at=row.imported_at,
        )


class ListingOut(ApiModel):
    id: str
    catalog_key: str
    grade_label: str
    platform: str | None = None
    price: float
    currency: str
    listing_title: str | None = None
    source_url: str | None = None
    is_auction: bool = False
    is_active: bool = True
    listed_at: date | None = None

    @classmethod
    def from_model(cls, row: MarketListing) -> ListingOut:
        return cls(
            id=row.id,
            catalog_key=row.catalog_key,
            grade_label=row.grade_label,
            platform=row.platform,
            price=to_major(row.price_minor) or 0.0,
            currency=row.currency,
            listing_title=row.listing_title,
            source_url=row.source_url,
            is_auction=row.is_auction,
            is_active=row.is_active,
            listed_at=row.listed_at,
        )


class PriceOut(ApiModel):
    """One computed valuation, with the evidence attached to it."""

    id: str
    catalog_key: str
    company_id: str | None = None
    grade: float | None = None
    grade_label: str
    currency: str
    median: float | None = None
    weighted_median: float | None = None
    mean: float | None = None
    low_quartile: float | None = None
    high_quartile: float | None = None
    last_sale: float | None = None
    realistic_sale: float | None = None
    quick_sale: float | None = None
    sample_size: int = 0
    window_days: int | None = None
    last_sale_at: date | None = None
    confidence: str = Confidence.NONE.value
    computed_at: datetime | None = None
    user_value: float | None = None
    user_value_note: str | None = None
    premium_vs_raw_pct: float | None = None

    @classmethod
    def from_model(cls, row: MarketPrice, premium: float | None = None) -> PriceOut:
        return cls(
            id=row.id,
            catalog_key=row.catalog_key,
            company_id=row.company_id,
            grade=row.grade,
            grade_label=row.grade_label,
            currency=row.currency,
            median=to_major(row.median_minor),
            weighted_median=to_major(row.weighted_median_minor),
            mean=to_major(row.mean_minor),
            low_quartile=to_major(row.low_quartile_minor),
            high_quartile=to_major(row.high_quartile_minor),
            last_sale=to_major(row.last_sale_minor),
            realistic_sale=to_major(row.realistic_sale_minor),
            quick_sale=to_major(row.quick_sale_minor),
            sample_size=row.sample_size,
            window_days=row.window_days,
            last_sale_at=row.last_sale_at,
            confidence=row.confidence,
            computed_at=row.computed_at,
            user_value=to_major(row.user_value_minor),
            user_value_note=row.user_value_note,
            premium_vs_raw_pct=premium,
        )


class PriceOverride(ApiModel):
    """Your own valuation for one grade. Stored beside the computed one, never over it."""

    value: float | None = Field(default=None, ge=0)
    note: str | None = None

    def value_minor(self) -> int | None:
        return to_minor(self.value)


class LiquidityOut(ApiModel):
    score: float | None = None
    band: str = LiquidityBand.UNKNOWN.value
    sales_7d: int = 0
    sales_30d: int = 0
    sales_90d: int = 0
    sales_365d: int = 0
    days_since_last_sale: int | None = None
    active_listings: int | None = None
    sold_to_active_ratio: float | None = None
    median_days_between_sales: float | None = None
    sales_per_month: float | None = None


class TrendOut(ApiModel):
    direction: str = TrendDirection.INSUFFICIENT_DATA.value
    confidence: str = Confidence.NONE.value
    grade_label: str | None = None
    change_7d_pct: float | None = None
    change_30d_pct: float | None = None
    change_90d_pct: float | None = None
    change_180d_pct: float | None = None
    change_365d_pct: float | None = None
    sample_size: int = 0


class MarketSummary(ApiModel):
    """Everything known about one identity's market, in one response."""

    catalog_key: str
    currency: str
    prices: list[PriceOut] = Field(default_factory=list)
    liquidity: LiquidityOut = Field(default_factory=LiquidityOut)
    trend: TrendOut = Field(default_factory=TrendOut)
    sale_count: int = 0
    excluded_count: int = 0
    grade_labels: list[str] = Field(default_factory=list)
    computed_at: datetime | None = None


class SnapshotPoint(ApiModel):
    snapshot_date: date
    value: float
    sample_size: int = 0
    active_listings: int | None = None


class SnapshotSeries(ApiModel):
    grade_label: str
    currency: str
    points: list[SnapshotPoint] = Field(default_factory=list)


class RowErrorOut(ApiModel):
    line_number: int | None = None
    message: str
    values: dict[str, str] = Field(default_factory=dict)


class ImportResult(ApiModel):
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    excluded: int = 0
    outliers_flagged: int = 0
    exclusions: dict[str, int] = Field(default_factory=dict)
    errors: list[RowErrorOut] = Field(default_factory=list)
    prices: list[PriceOut] = Field(default_factory=list)


class CsvImportRequest(ApiModel):
    """A pasted or uploaded CSV, imported against one card's identity."""

    csv: str = Field(min_length=1, description="Raw CSV text, with a header row.")
    day_first: bool = Field(
        default=True,
        description=(
            "How to read 03/04/2025. A file is written one way throughout, so this is set per "
            "import rather than guessed per row."
        ),
    )
    apply_filters: bool = True


class ReclassifyResult(ApiModel):
    kept: int = 0
    excluded: int = 0
    unchanged: int = 0
    skipped_user: int = 0
    outliers_flagged: int = 0
    outliers_cleared: int = 0
