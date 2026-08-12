"""PriceCharting — graded prices, which is the number the decision turns on.

Grading is a comparison between what a card fetches raw and what it fetches in
a slab. Every other source here supplies only the first half: the catalogue has
no graded prices at all, and eBay's sold data sits behind an approval most
accounts do not get. This one returns the graded ladder as named fields, for a
paid key and no approval committee — a lesser API you can actually have,
beating a better one you cannot.

**The mapping is configuration, and that is not a stylistic choice.**

PriceCharting is a video-game price guide that also covers cards, and it reuses
the game condition fields for card grades: ``box-only-price`` and
``manual-only-price`` hold grades, not boxes and manuals. Which field means
which grade is the single most important fact in this module and the one this
build has never been able to verify — the sandbox cannot reach the site, and the
published documentation does not spell the mapping out.

So it lives in ``grade_fields`` on the data source, ships marked unconfirmed,
and **no graded price is written until it is confirmed**. Getting this wrong
would not break anything visibly. It would put a real number from the wrong
grade beside the raw price and quietly invert the recommendation, which is worse
than returning nothing at all.

``make pricecharting-fields`` prints what the API actually returned next to what
the mapping claims, so confirming it is half a minute of looking at the card's
own page rather than an act of faith.

**What it does not give.** No individual sales — these are PriceCharting's own
aggregates over recent eBay sold listings — so a price arrives with no sample
size, liquidity stays unknown, and trend still accrues forward from snapshots.
It answers *what is a slab worth*, not *how easily does it sell*.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.market_data.base import (
    CardMatch,
    CardQuery,
    MarketDataProvider,
    MarketKey,
    PricePoint,
    ProviderCapabilities,
)
from app.services.market_data.http import HttpxTransport, ProviderRequestError, Transport

__all__ = ["PriceChartingProvider"]

DEFAULT_BASE_URL = "https://www.pricecharting.com"

#: Quoted in USD, always, whatever the marketplace the sales came from.
PRICE_CURRENCY = "USD"

#: A paid key, so the limit is generous. Still a background job, not a race.
DEFAULT_RATE_LIMIT = 60

#: Provisional. Widely reported, never verified by this build, and deliberately
#: inert until someone says otherwise — see ``mapping_confirmed``.
#:
#: ``loose-price`` is the exception: "loose" meaning ungraded is unambiguous on
#: a price guide that started with cartridges, and it is the one entry here that
#: is not a reuse of a video-game field name for something else entirely.
DEFAULT_GRADE_FIELDS: dict[str, str] = {
    "loose-price": "raw",
    "graded-price": "PSA 9",
    "box-only-price": "PSA 9.5",
    "manual-only-price": "PSA 10",
    "bgs-10-price": "BGS 10",
}

#: Fields that are certainly not grades, so a mapping naming one is a mistake
#: rather than a preference.
_NON_PRICE_FIELDS = {"id", "product-name", "console-name", "release-date", "status"}


class PriceChartingProvider(MarketDataProvider):
    code = "pricecharting"
    name = "PriceCharting"

    # It has its own product ids and its own search, so a card is matched once
    # and the id stored — the same flow as the catalogue. Re-searching by name
    # on every sync would risk drifting onto a different printing.
    requires_external_id = True

    def __init__(
        self,
        config: dict | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(config=config, api_key=api_key)
        self.base_url = (self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.grade_fields: dict[str, str] = dict(
            self.config.get("grade_fields") or DEFAULT_GRADE_FIELDS
        )
        bad = sorted(set(self.grade_fields) & _NON_PRICE_FIELDS)
        if bad:
            raise ValueError(
                f"grade_fields maps {', '.join(bad)}, which are not prices. "
                "It maps a JSON price field to a grade label, e.g. "
                '{"manual-only-price": "PSA 10"}.'
            )
        self.transport = transport or HttpxTransport(
            rate_limit_per_minute=self.config.get("rate_limit_per_minute") or DEFAULT_RATE_LIMIT
        )

    @property
    def mapping_confirmed(self) -> bool:
        """Has a human checked which field is which grade?

        Default false, and the graded half of this source stays switched off
        until it is true. An unconfirmed mapping is not a small risk taken for
        convenience: it is a plausible number attached to the wrong grade, which
        is indistinguishable from a correct one and flips the recommendation.
        """
        return bool(self.config.get("grade_fields_confirmed"))

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            search=True,
            current_price=True,
            # Aggregates over other people's sold listings, not the listings
            # themselves. Nothing here can populate market_sales.
            sales_history=False,
            active_listings=False,
            graded_prices=self.mapping_confirmed,
            historical_series=False,
        )

    @property
    def price_currency(self) -> str:
        return PRICE_CURRENCY

    def available_grade_labels(self) -> list[str]:
        """Raw always; the graded ladder only once the mapping is confirmed.

        This is what the sync iterates over, so an unconfirmed mapping does not
        merely refuse to answer for a grade — it never gets asked, and no
        half-written graded row can appear.
        """
        labels = ["raw"]
        if self.mapping_confirmed:
            labels.extend(
                str(label)
                for label in self.grade_fields.values()
                if str(label).strip().lower() != "raw"
            )
        return list(dict.fromkeys(labels))

    # --- Search --------------------------------------------------------------

    def search_card(self, query: CardQuery) -> list[CardMatch]:
        terms = _search_terms(query)
        if not terms:
            return []
        body = self._get("/api/products", {"q": terms})
        products = body.get("products")
        if not isinstance(products, list):
            return []
        return [match for match in (_to_match(item, query) for item in products) if match][
            : max(query.limit, 1)
        ]

    # --- Prices --------------------------------------------------------------

    def get_current_price(self, key: MarketKey) -> PricePoint | None:
        """The price for one grade, or nothing.

        ``None`` rather than a fallback in every doubtful case. A price guide
        that cannot answer for PSA 10 must not answer with the raw price: that
        would put the same number on both sides of the grading decision and make
        grading look exactly break-even, every time.
        """
        if not key.external_id:
            return None
        field = self._field_for(key.grade_label)
        if field is None:
            return None

        product = self._product(key.external_id)
        if product is None:
            return None
        value_minor = _price_minor(product.get(field))
        if value_minor is None:
            return None

        return PricePoint(
            value_minor=value_minor,
            currency=PRICE_CURRENCY,
            # Left unknown on purpose. The response carries the card's *release*
            # date, which is not when the price was computed, and using it would
            # date a figure from today as though it were from 2021.
            as_of=None,
            # Zero, and it must stay zero. This is somebody's aggregate, not a
            # count of sales you could have made, and every confidence reading
            # downstream keys off it.
            sample_size=0,
            raw={
                "field": field,
                "grade_label": key.grade_label,
                "product_id": product.get("id"),
                "product_name": product.get("product-name"),
                "set": product.get("console-name"),
            },
        )

    def _field_for(self, grade_label: str) -> str | None:
        """Which JSON field holds this grade, if we are allowed to say."""
        wanted = (grade_label or "raw").strip().lower()
        for field, label in self.grade_fields.items():
            if str(label).strip().lower() != wanted:
                continue
            if wanted != "raw" and not self.mapping_confirmed:
                # The raw price is safe: "loose" is unambiguous. Everything else
                # rests on a mapping nobody has checked yet.
                return None
            return field
        return None

    def fields_for_product(self, external_id: str) -> dict[str, Any]:
        """Every price the API returned, for the confirmation command.

        Deliberately unmapped and unfiltered. The whole point is to show what
        arrived so a person can compare it against the card's own page, and a
        helpfully tidied version would hide the disagreement being looked for.
        """
        product = self._product(external_id)
        return dict(product) if product else {}

    def _product(self, external_id: str) -> dict | None:
        body = self._get("/api/product", {"id": external_id})
        return body if body.get("id") is not None else None

    # --- Plumbing ------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        if not self.api_key:
            raise ProviderRequestError(
                "PriceCharting needs SLABSTACK_PRICECHARTING_API_KEY in the environment. "
                "It comes with a paid subscription — there is no anonymous tier."
            )
        body = self.transport.get_json(
            f"{self.base_url}{path}", params={**params, "t": self.api_key}, headers={}
        )
        status = body.get("status")
        if status and status != "success":
            raise ProviderRequestError(
                f"PriceCharting refused the request: {body.get('error-message') or status}."
            )
        return body


# --- Query building ----------------------------------------------------------


def _search_terms(query: CardQuery) -> str:
    """One text query, which is all this API takes.

    The set matters more here than it does elsewhere: PriceCharting keys a card
    by its set ("Pokemon Evolving Skies"), and a bare name returns every
    Umbreon ever printed.
    """
    parts = [(query.name or query.text or "").strip()]
    if query.set_name:
        parts.append(query.set_name.strip())
    if query.card_number:
        parts.append(query.card_number.split("/")[0].strip())
    return " ".join(part for part in parts if part)[:200]


def _to_match(item: Any, query: CardQuery) -> CardMatch | None:
    if not isinstance(item, dict) or not item.get("id") or not item.get("product-name"):
        return None
    return CardMatch(
        external_id=str(item["id"]),
        name=str(item["product-name"]),
        # PriceCharting calls the set the "console", which it is for a cartridge.
        set_name=item.get("console-name"),
        confidence=_match_confidence(item, query),
        raw={"id": item.get("id"), "console": item.get("console-name")},
    )


def _match_confidence(item: dict, query: CardQuery) -> float:
    score = 0.0
    name = str(item.get("product-name") or "").lower()
    wanted = (query.name or query.text or "").lower().strip()
    if wanted and wanted == name:
        score += 0.5
    elif wanted and wanted in name:
        score += 0.3

    if query.card_number:
        number = query.card_number.split("/")[0].strip()
        if number and number in name:
            score += 0.2

    set_name = str(item.get("console-name") or "").lower()
    if query.set_name and query.set_name.lower() in set_name:
        score += 0.2
    return round(min(score, 1.0), 2)


# --- Parsing -----------------------------------------------------------------


def _price_minor(value: Any) -> int | None:
    """Prices arrive as integers in cents, which is what this app stores.

    No conversion, and none wanted: turning 17244 into 172.44 and back is two
    chances to lose a penny for no benefit.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        if not isinstance(value, str) or not value.strip().isdigit():
            return None
        value = int(value)
    minor = int(value)
    # Zero means "not enough sales at this grade", which every card has at some
    # grade. It is an absence, not a price of nothing.
    return minor if minor > 0 else None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
