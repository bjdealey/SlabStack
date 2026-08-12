"""eBay — individual sold listings, which is what these engines were built for.

Every other source so far supplies an *index*: one number, someone else's
average, with no evidence behind it. This supplies the evidence. That difference
is the reason to want it, and it shows up in three places at once:

* **Liquidity** becomes measurable. It is computed from how often a card
  actually trades and how the sold count compares to the active one, and no
  aggregate price can answer either question.
* **Trend** gets a history instead of accruing forward a day at a time.
* **The grading decision becomes answerable.** Slabs are sold on eBay with the
  grade in the title, so a single query returns raw sales *and* PSA 10 sales
  *and* CGC 9.5 sales, which is exactly the comparison the whole application
  exists to make.

The third one is why this source was worth the work. Until now the graded side
of that comparison had to be typed in by hand.

**What it cannot do, stated as plainly as what it can.**

*Marketplace Insights is not granted to every application.* Sold data lives
behind ``buy.marketplace.insights``, which eBay approves case by case. Without
it every sold query returns 403. That is not a bug and this adapter does not
paper over it: it says so, and falls back to the Browse API, which is available
to any developer account.

*Ninety days, and no further back.* Insights covers the last 90 days. There is
no deep history to import here, so a two-year trend still has to accrue from
daily snapshots.

*Asking prices are not sales.* Browse returns what sellers are asking, which is
a different and much more optimistic number than what buyers paid. Those go to
``market_listings`` and are never written as sales. They earn their place by
feeding the sold-to-active ratio — the denominator liquidity needs.

*The grade is read out of somebody else's sentence.* eBay has no grade field;
the grade is whatever the seller typed in the title. ``parse_grade_from_title``
does the reading, including refusing the "PSA 10 READY" titles that describe a
raw card in the language of a slab.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.services.market_data.base import (
    CardQuery,
    ListingRecord,
    MarketDataProvider,
    MarketKey,
    ProviderCapabilities,
    SaleRecord,
)
from app.services.market_data.http import (
    CapabilityDeniedError,
    HttpxTransport,
    ProviderRequestError,
    Transport,
)

__all__ = ["EbayProvider"]

DEFAULT_BASE_URL = "https://api.ebay.com"
DEFAULT_MARKETPLACE = "EBAY_GB"

#: eBay quotes each marketplace in its own currency. Used for the up-front
#: "will this need an exchange rate?" check; each item's own currency is what
#: actually gets recorded, because trusting the map over the payload would be
#: inventing a fact the response already states.
MARKETPLACE_CURRENCY = {
    "EBAY_GB": "GBP",
    "EBAY_US": "USD",
    "EBAY_IE": "EUR",
    "EBAY_DE": "EUR",
    "EBAY_FR": "EUR",
    "EBAY_IT": "EUR",
    "EBAY_ES": "EUR",
    "EBAY_NL": "EUR",
    "EBAY_AT": "EUR",
    "EBAY_BE": "EUR",
    "EBAY_AU": "AUD",
    "EBAY_CA": "CAD",
    "EBAY_CH": "CHF",
    "EBAY_PL": "PLN",
}

#: eBay's own ceiling for these endpoints.
MAX_PAGE_SIZE = 200

#: Marketplace Insights covers the last 90 days and no more. Asking for more is
#: not an error, it just returns nothing older, so the window is stated here
#: rather than discovered as a puzzling gap in the data.
SOLD_WINDOW_DAYS = 90

#: Deliberately conservative: this is somebody's production API and a sync is a
#: background job, not a race.
DEFAULT_RATE_LIMIT = 30

#: Refresh a token slightly before it expires rather than after, so a long sync
#: does not fail its last request on a boundary.
_TOKEN_SAFETY_SECONDS = 60

_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"

#: Why no seller username is ever recorded from this source.
#:
#: eBay requires every application to either subscribe to marketplace account
#: deletion notifications — pushing to a public HTTPS endpoint, which a
#: localhost-only tool cannot have — or to declare that it does not persist eBay
#: user data. The second is the only route open to this build, and it has to be
#: *true*, not merely convenient.
#:
#: A seller's username is the one field here that identifies a person. Prices,
#: dates, item ids and listing titles describe a listing; a username describes
#: somebody. Nothing in this application reads it — no engine, no filter, no
#: score — so it was pure liability, and dropping it costs nothing and makes the
#: declaration honest.
#:
#: A seller name you type in yourself on a manual sale is untouched by this. It
#: is your record of your own transaction, and it never came from eBay.
_NO_SELLER = None


class EbayProvider(MarketDataProvider):
    code = "ebay"
    name = "eBay"

    # A marketplace has no card ids — it is queried by name, like a person would
    # query it. So unlike a catalogue source, this can sync a card that has
    # never been linked to anything.
    requires_external_id = False

    def __init__(
        self,
        config: dict | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(config=config, api_key=api_key)
        self.base_url = (self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.marketplace = (self.config.get("marketplace") or DEFAULT_MARKETPLACE).upper()
        if self.marketplace not in MARKETPLACE_CURRENCY:
            raise ValueError(
                f"'{self.marketplace}' is not an eBay marketplace this adapter knows. "
                f"Choose one of: {', '.join(sorted(MARKETPLACE_CURRENCY))}."
            )

        # The client secret, like the client id, is read from the environment by
        # *name*. The database stores which variable to look in and never the
        # value — that rule is what makes the config file safe to back up.
        self.api_secret_env_var = self.config.get("api_secret_env_var") or "SLABSTACK_EBAY_CERT_ID"
        self.api_secret = os.environ.get(self.api_secret_env_var)

        self.sold_window_days = int(self.config.get("sold_window_days") or SOLD_WINDOW_DAYS)
        # Empty by default, and that is a decision rather than an oversight. A
        # category filter would sharpen every query, but a *wrong* category id
        # returns zero results and looks exactly like "this card never sells".
        # This build has never been able to reach eBay to confirm one, so it
        # ships unset and documented instead of guessed.
        self.category_ids = self.config.get("category_ids") or ""

        self.transport = transport or HttpxTransport(
            rate_limit_per_minute=self.config.get("rate_limit_per_minute") or DEFAULT_RATE_LIMIT
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # Not a card catalogue. eBay knows about listings, not about card
            # identities, so putting it behind "find this card" would answer a
            # question about what a card *is* with a list of things for sale.
            search=False,
            # No aggregate index, and none is wanted. This source hands over
            # sales; the app computes the price from them, which is why that
            # price arrives with a sample size and a window attached.
            current_price=False,
            sales_history=True,
            active_listings=True,
            graded_prices=True,
            historical_series=False,
        )

    @property
    def price_currency(self) -> str:
        return MARKETPLACE_CURRENCY[self.marketplace]

    @property
    def sold_window(self) -> int:
        return self.sold_window_days

    # --- Sales ---------------------------------------------------------------

    def get_sales_history(self, key: MarketKey, since: date | None = None) -> list[SaleRecord]:
        """Completed sales for one card, newest first.

        Raises ``CapabilityDeniedError`` when the application has not been
        granted Marketplace Insights. That is deliberately not swallowed into an
        empty list: "you are not approved for this" and "this card has not sold"
        need completely different things from the user, and an empty list would
        make them indistinguishable.
        """
        return self.sales_for_query(_require_query(key), since=since)

    def sales_for_query(
        self, query: CardQuery, *, since: date | None = None, limit: int = 100
    ) -> list[SaleRecord]:
        """Completed sales matching a card, newest first."""
        terms = _search_terms(query)
        if not terms:
            return []

        window_start = since or (date.today() - timedelta(days=self.sold_window_days))
        params: dict[str, Any] = {
            "q": terms,
            "limit": min(max(limit, 1), MAX_PAGE_SIZE),
            "filter": f"lastSoldDate:[{_ebay_instant(window_start)}..]",
        }
        if self.category_ids:
            params["category_ids"] = self.category_ids

        body = self._get("/buy/marketplace_insights/v1_beta/item_sales/search", params)
        items = body.get("itemSales")
        if not isinstance(items, list):
            return []
        return [record for record in (self._to_sale(item) for item in items) if record]

    def _to_sale(self, item: Any) -> SaleRecord | None:
        if not isinstance(item, dict):
            return None
        price_minor, currency = _money(item.get("lastSoldPrice"))
        sold_on = _parse_instant(item.get("lastSoldDate"))
        if price_minor is None or sold_on is None:
            # A sale with no price or no date cannot be used by anything
            # downstream, and a zero or a today's-date stand-in would be worse
            # than the missing row.
            return None

        options = item.get("buyingOptions") or []
        shipping_minor, _ = _money(_first_shipping(item))

        return SaleRecord(
            sale_date=sold_on,
            price_minor=price_minor,
            currency=currency or self.price_currency,
            shipping_minor=shipping_minor,
            platform="eBay",
            listing_title=_text(item.get("title")),
            source_url=_text(item.get("itemWebUrl")),
            # Deliberately dropped, not forgotten. See _NO_SELLER below.
            seller=None,
            external_id=_text(item.get("itemId")),
            lot_size=1,
            is_auction="AUCTION" in options,
            raw={
                "buying_options": options,
                "condition": item.get("condition"),
                "categories": item.get("categories"),
                "image": (item.get("image") or {}).get("imageUrl"),
                "marketplace": self.marketplace,
                # Kept because eBay does not always distinguish an accepted
                # best offer from the price it was listed at. Where this says
                # BEST_OFFER the figure may be the asking price rather than the
                # price paid, and the row is inspectable rather than silently
                # trusted.
                "quantity_sold": item.get("lastSoldQuantity"),
            },
        )

    # --- Active listings -----------------------------------------------------

    def get_listings(self, key: MarketKey) -> list[ListingRecord]:
        """What is on sale right now for one card."""
        return self.listings_for_query(_require_query(key))

    def listings_for_query(self, query: CardQuery, *, limit: int = 100) -> list[ListingRecord]:
        """What is on sale right now — asking prices, never recorded as sales."""
        terms = _search_terms(query)
        if not terms:
            return []

        params: dict[str, Any] = {
            "q": terms,
            "limit": min(max(limit, 1), MAX_PAGE_SIZE),
        }
        if self.category_ids:
            params["category_ids"] = self.category_ids

        body = self._get("/buy/browse/v1/item_summary/search", params)
        items = body.get("itemSummaries")
        if not isinstance(items, list):
            return []

        # How many eBay says exist, which is not how many came back. It matters
        # because the sold-to-active ratio divides by this: counting only the
        # page we fetched would understate supply and flatter liquidity, and
        # liquidity flatters the decision to grade. Carried per record because
        # the interface returns records, and a fact about the result set has
        # nowhere else to travel.
        total = body.get("total")
        reported = int(total) if isinstance(total, int | float) else None

        records = [record for record in (self._to_listing(item) for item in items) if record]
        for record in records:
            record.raw["result_total"] = reported
        return records

    def _to_listing(self, item: Any) -> ListingRecord | None:
        if not isinstance(item, dict):
            return None
        price_minor, currency = _money(item.get("price"))
        if price_minor is None:
            return None

        options = item.get("buyingOptions") or []
        shipping_minor, _ = _money(_first_shipping(item))

        return ListingRecord(
            price_minor=price_minor,
            currency=currency or self.price_currency,
            listed_at=_parse_instant(item.get("itemCreationDate")),
            platform="eBay",
            listing_title=_text(item.get("title")),
            source_url=_text(item.get("itemWebUrl")),
            # Deliberately dropped, not forgotten. See _NO_SELLER below.
            seller=None,
            external_id=_text(item.get("itemId")),
            is_auction="AUCTION" in options,
            raw={
                "buying_options": options,
                "condition": item.get("condition"),
                "shipping_minor": shipping_minor,
                "marketplace": self.marketplace,
            },
        )

    # --- Authentication ------------------------------------------------------

    def access_token(self) -> str:
        """A client-credentials token, cached until shortly before it expires.

        Application tokens, not user ones: this reads public marketplace data
        and never acts on anyone's account. There is no consent flow to complete
        and nothing here can list, bid or buy.
        """
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        if not self.api_key or not self.api_secret:
            missing = "SLABSTACK_EBAY_APP_ID" if not self.api_key else self.api_secret_env_var
            raise ProviderRequestError(
                f"eBay needs both a client id and a client secret; {missing} is not set. "
                "Create an application at developer.ebay.com — the App ID is the client id and "
                "the Cert ID is the client secret."
            )

        credentials = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
        body = self.transport.post_form(
            f"{self.base_url}/identity/v1/oauth2/token",
            data={"grant_type": "client_credentials", "scope": _OAUTH_SCOPE},
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderRequestError(
                "eBay accepted the credentials but returned no access token. Check that the "
                "application is active and that the keys are the production pair, not sandbox."
            )
        expires_in = body.get("expires_in")
        seconds = float(expires_in) if isinstance(expires_in, int | float) else 7200.0
        self._token = token
        self._token_expires_at = time.monotonic() + max(seconds - _TOKEN_SAFETY_SECONDS, 0.0)
        return token

    # --- Plumbing ------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        headers = {
            "Authorization": f"Bearer {self.access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
        }
        try:
            return self.transport.get_json(f"{self.base_url}{path}", params=params, headers=headers)
        except ProviderRequestError as exc:
            if exc.status_code == 403 and "marketplace_insights" in path:
                raise CapabilityDeniedError(
                    "eBay refused the sold-listings request (403). Marketplace Insights is "
                    "granted per application: apply for the buy.marketplace.insights scope at "
                    "developer.ebay.com. Active listings still work without it, so liquidity "
                    "keeps its denominator — but there are no sold prices until it is approved.",
                    status_code=403,
                ) from exc
            raise


# --- Query building ----------------------------------------------------------


def _require_query(key: MarketKey) -> CardQuery:
    """A marketplace needs something to search for, and it is not a catalog key.

    Loud rather than empty. A silent ``[]`` here would read all the way up the
    stack as "this card has never sold on eBay", which is a claim about the
    market rather than what it actually is — a caller that forgot to say which
    card it meant.
    """
    if key.query is None:
        raise ProviderRequestError(
            "eBay is searched by name and was given no card to search for. The sync engine "
            "attaches a CardQuery to the MarketKey for sources that have no id of their own."
        )
    return key.query


def _search_terms(query: CardQuery) -> str:
    """What a person would actually type into eBay's search box.

    Narrow enough to find the card, loose enough that the slabs come back too:
    no grade terms, because a query naming one grade hides every other, and the
    point of one broad query is that raw and graded sales arrive together and
    can be compared.
    """
    parts: list[str] = []
    name = (query.name or query.text or "").strip()
    if not name:
        return ""
    parts.append(name)

    if query.card_number:
        # "215/203" is how listings write it, and how eBay tokenises it.
        parts.append(query.card_number.strip())
    elif query.set_name:
        parts.append(query.set_name.strip())

    return " ".join(part for part in parts if part)[:350]


# --- Parsing -----------------------------------------------------------------


def _money(block: Any) -> tuple[int | None, str | None]:
    """eBay states money as ``{"value": "410.00", "currency": "GBP"}``.

    Always a dot-decimal string whatever the marketplace, so this parses
    strictly rather than going through the loose importer parser — which reads
    "1.234,56" as European and would turn £1.99 into £199 given the chance.
    """
    if not isinstance(block, dict):
        return None, None
    value = block.get("value")
    currency = block.get("currency")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None, None
    if not isinstance(value, int | float):
        return None, None
    if value <= 0:
        # Zero is not a price. It means the field was present and empty, and
        # writing it as £0.00 would drag every median it touches.
        return None, (currency if isinstance(currency, str) else None)
    return round(float(value) * 100), (currency if isinstance(currency, str) else None)


def _first_shipping(item: dict) -> dict | None:
    options = item.get("shippingOptions")
    if isinstance(options, list) and options and isinstance(options[0], dict):
        cost = options[0].get("shippingCost")
        return cost if isinstance(cost, dict) else None
    return None


def _parse_instant(value: Any) -> date | None:
    """``2026-07-14T10:22:31.000Z`` to a date. Anything else stays unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _ebay_instant(value: date) -> str:
    """The RFC 3339 form eBay's date filters take."""
    return datetime(value.year, value.month, value.day, tzinfo=UTC).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
