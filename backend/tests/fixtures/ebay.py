"""Recorded eBay responses, shaped as the Buy APIs document them.

Hand-built rather than captured, for the same reason as the catalogue fixtures
and with the same caveat attached: this proves the adapter reads *this* shape
correctly, and cannot prove eBay still returns it. The first real request is the
verification and it happens on the user's machine.

What these do prove is the part that matters most, because it is the part a live
API would never have shown us either — that a page of real marketplace results
is mostly things you must not count. The sold set below is deliberately eight
listings of which only three are usable comparables for an English alternate-art
Umbreon VMAX: the rest are a job lot, a Japanese copy, a reverse holo, a raw
card advertised as "PSA 10 READY", and two rows too broken to use. That ratio is
not pessimism. It is what a marketplace search actually looks like.
"""

from __future__ import annotations

BASE = "https://api.ebay.com"
TOKEN_URL = f"{BASE}/identity/v1/oauth2/token"
SOLD_URL = f"{BASE}/buy/marketplace_insights/v1_beta/item_sales/search"
BROWSE_URL = f"{BASE}/buy/browse/v1/item_summary/search"

TOKEN = {
    "access_token": "v^1.1#i^1#recorded-application-token",
    "expires_in": 7200,
    "token_type": "Application Access Token",
}


def _sale(
    item_id: str,
    title: str,
    value: str,
    sold_on: str,
    *,
    currency: str = "GBP",
    options: list[str] | None = None,
    seller: str = "cardshop_uk",
) -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "lastSoldPrice": {"value": value, "currency": currency},
        "lastSoldDate": sold_on,
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{item_id.split('|')[1]}",
        "seller": {"username": seller},
        "condition": "Used",
        "buyingOptions": options or ["FIXED_PRICE"],
        "image": {"imageUrl": "https://i.ebayimg.com/images/g/recorded/s-l500.jpg"},
    }


#: The graded comparable. This is the number the whole grading decision needs,
#: and until this source existed it had to be typed in by hand.
PSA_10 = _sale(
    "v1|294001|0",
    "Pokemon Umbreon VMAX Alt Art 215/203 Evolving Skies PSA 10 GEM MINT",
    "420.00",
    "2026-07-14T10:22:31.000Z",
)

#: A second grader, so the ladder has more than one rung.
CGC_95 = _sale(
    "v1|294002|0",
    "Umbreon VMAX Alternate Art 215/203 Evolving Skies CGC 9.5 Mint+",
    "305.00",
    "2026-07-02T18:04:00.000Z",
)

#: The raw side of the comparison.
RAW = _sale(
    "v1|294003|0",
    "Pokemon Umbreon VMAX Alternate Art 215/203 Evolving Skies NM",
    "215.00",
    "2026-07-20T09:15:00.000Z",
    options=["FIXED_PRICE", "BEST_OFFER"],
)

#: The trap. A raw card, advertised in the language of a slab. Counted as a
#: PSA 10 it would drag the graded price from £420 towards £230 and quietly
#: turn a profitable grade into a "not worth it".
ASPIRATIONAL = _sale(
    "v1|294004|0",
    "Umbreon VMAX Alt Art 215/203 PSA 10 READY Gem Mint centering!",
    "230.00",
    "2026-07-18T12:00:00.000Z",
)

#: Excluded: several cards for one price.
JOB_LOT = _sale(
    "v1|294005|0",
    "Pokemon Evolving Skies job lot 50 cards inc Umbreon VMAX Alt Art",
    "560.00",
    "2026-07-11T20:30:00.000Z",
)

#: Excluded: a different market that happens to share the artwork.
JAPANESE = _sale(
    "v1|294006|0",
    "Japanese Umbreon VMAX Alt Art 215/203 Eevee Heroes NM",
    "180.00",
    "2026-07-09T08:00:00.000Z",
)

#: Excluded: a different printing of the same card.
REVERSE_HOLO = _sale(
    "v1|294007|0",
    "Umbreon VMAX 215/203 Reverse Holo Evolving Skies",
    "95.00",
    "2026-07-05T14:45:00.000Z",
)

#: Dropped before it reaches the importer: no price at all.
NO_PRICE = {
    "itemId": "v1|294008|0",
    "title": "Umbreon VMAX Alt Art 215/203",
    "lastSoldDate": "2026-07-01T10:00:00.000Z",
    "itemWebUrl": "https://www.ebay.co.uk/itm/294008",
}

#: Dropped: a price with no date is not a point on any timeline.
NO_DATE = {
    "itemId": "v1|294009|0",
    "title": "Umbreon VMAX Alt Art 215/203",
    "lastSoldPrice": {"value": "199.00", "currency": "GBP"},
    "itemWebUrl": "https://www.ebay.co.uk/itm/294009",
}

#: A zero price means the field was present and empty, not that it was free.
ZERO_PRICE = _sale(
    "v1|294010|0",
    "Umbreon VMAX Alt Art 215/203 Evolving Skies",
    "0.00",
    "2026-06-28T10:00:00.000Z",
)

SOLD_ITEMS = [
    PSA_10,
    CGC_95,
    RAW,
    ASPIRATIONAL,
    JOB_LOT,
    JAPANESE,
    REVERSE_HOLO,
    NO_PRICE,
    NO_DATE,
    ZERO_PRICE,
]


def sold_response(*items: dict) -> dict:
    chosen = list(items) if items else SOLD_ITEMS
    return {
        "itemSales": chosen,
        "href": f"{SOLD_URL}?q=Umbreon%20VMAX",
        "total": len(chosen),
        "limit": 100,
        "offset": 0,
    }


def _listing(item_id: str, title: str, value: str, *, options: list[str] | None = None) -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "price": {"value": value, "currency": "GBP"},
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{item_id.split('|')[1]}",
        "itemCreationDate": "2026-08-01T09:00:00.000Z",
        "seller": {"username": "seller_two"},
        "condition": "Used",
        "buyingOptions": options or ["FIXED_PRICE"],
        "shippingOptions": [{"shippingCost": {"value": "2.95", "currency": "GBP"}}],
    }


ACTIVE = [
    _listing("v1|395001|0", "Umbreon VMAX Alt Art 215/203 PSA 10 GEM MINT", "499.00"),
    _listing("v1|395002|0", "Umbreon VMAX Alternate Art 215/203 NM", "249.99"),
    _listing("v1|395003|0", "Umbreon VMAX Alt Art 215/203", "239.00", options=["AUCTION"]),
]


def browse_response(*items: dict, total: int | None = None) -> dict:
    chosen = list(items) if items else ACTIVE
    return {
        "itemSummaries": chosen,
        "href": f"{BROWSE_URL}?q=Umbreon%20VMAX",
        # Deliberately larger than the page by default. Liquidity divides by the
        # active count, so a page mistaken for the whole market would understate
        # supply — and understated supply flatters the decision to grade.
        "total": total if total is not None else 61,
        "limit": 100,
        "offset": 0,
    }


def transport(*, sold: dict | None = None, browse: dict | None = None, **extra: object):
    """A RecordedTransport preloaded with the token and both searches.

    Pass an ``Exception`` for either search to record a failure — a 403 from
    Marketplace Insights is behaviour worth testing, not an absence of it.
    """
    from app.services.market_data.http import RecordedTransport

    responses: dict = {
        TOKEN_URL: TOKEN,
        SOLD_URL: sold if sold is not None else sold_response(),
        BROWSE_URL: browse if browse is not None else browse_response(),
    }
    responses.update(extra)
    return RecordedTransport(responses=responses)
