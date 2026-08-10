"""Market-data provider abstraction (spec sections 14, 37).

No provider is implemented against a network in Phase 1 — this file exists now
so that Phase 3 adds adapters *behind* an interface the rest of the application
is already written against. Two rules the adapters must keep:

1. Providers only ever write into the local tables. Nothing downstream of the
   database is allowed to call a provider directly, so an API going away costs
   the user future updates and never their history.
2. Providers use official, permitted APIs under the terms of the service. A
   provider that requires scraping a site that forbids it does not belong here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class ProviderCapabilities:
    search: bool = False
    current_price: bool = False
    sales_history: bool = False
    active_listings: bool = False
    graded_prices: bool = False
    historical_series: bool = False


@dataclass(frozen=True)
class CardQuery:
    """What the user typed, plus anything we already know."""

    text: str | None = None
    name: str | None = None
    set_code: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    variant: str | None = None
    language: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class CardMatch:
    """A candidate identification. Never applied without user confirmation (§5)."""

    external_id: str
    name: str
    set_name: str | None = None
    set_code: str | None = None
    card_number: str | None = None
    variant: str | None = None
    language: str | None = None
    rarity: str | None = None
    image_url: str | None = None
    confidence: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MarketKey:
    """Identity + grade being priced."""

    catalog_key: str
    grade_label: str = "raw"
    company_code: str | None = None
    grade: float | None = None
    currency: str = "GBP"


@dataclass(frozen=True)
class SaleRecord:
    sale_date: date
    price_minor: int
    currency: str = "GBP"
    shipping_minor: int | None = None
    platform: str | None = None
    listing_title: str | None = None
    source_url: str | None = None
    seller: str | None = None
    external_id: str | None = None
    lot_size: int = 1
    is_auction: bool | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ListingRecord:
    price_minor: int
    currency: str = "GBP"
    listed_at: date | None = None
    platform: str | None = None
    listing_title: str | None = None
    source_url: str | None = None
    seller: str | None = None
    external_id: str | None = None
    is_auction: bool = False
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PricePoint:
    value_minor: int
    currency: str = "GBP"
    as_of: date | None = None
    sample_size: int = 0
    raw: dict = field(default_factory=dict)


class MarketDataProvider(ABC):
    """One adapter per data source.

    Implementations must be side-effect free with respect to the database: they
    return records, and the import service decides what to persist, deduplicate
    and exclude.
    """

    code: str = "abstract"
    name: str = "Abstract provider"

    def __init__(self, config: dict | None = None, api_key: str | None = None) -> None:
        self.config = config or {}
        self.api_key = api_key

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    def search_card(self, query: CardQuery) -> list[CardMatch]:
        raise NotImplementedError(f"{self.code} does not support card search")

    def get_current_price(self, key: MarketKey) -> PricePoint | None:
        raise NotImplementedError(f"{self.code} does not support current prices")

    def get_sales_history(self, key: MarketKey, since: date | None = None) -> list[SaleRecord]:
        raise NotImplementedError(f"{self.code} does not support sales history")

    def get_listings(self, key: MarketKey) -> list[ListingRecord]:
        raise NotImplementedError(f"{self.code} does not support active listings")
