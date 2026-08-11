"""The always-available provider: whatever the user typed in.

Manual entry is a first-class source, not a fallback. It is the only one that
works with no network, no API key and no terms of service, and it is what the
whole application degrades to when every external provider is unavailable.

It reads from the local tables, so it is implemented as a thin marker: the
import and pricing services already read those tables directly.
"""

from __future__ import annotations

from app.services.market_data.base import MarketDataProvider, ProviderCapabilities


class ManualProvider(MarketDataProvider):
    code = "manual"
    name = "Manual entry"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            search=False,
            current_price=True,
            sales_history=True,
            active_listings=True,
            graded_prices=True,
            historical_series=True,
        )
