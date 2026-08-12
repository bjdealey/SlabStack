"""Recorded PriceCharting responses, shaped as the API documents them.

Hand-built, and carrying an unusually large caveat. The field *names* and units
below are documented and verifiable: prices are integers in pennies, auth is a
``t`` query parameter, and the JSON keys match the CSV price-guide columns.

What is **not** verified is which field means which grade. PriceCharting reuses
its video-game condition fields for cards, and the mapping is not spelled out in
the documentation this build could reach. So the numbers below are deliberately
spread far apart — $215, $305, $420, $960 — because the point of these fixtures
is to prove the adapter reads whichever field it was *told* to read, not to
bless a particular reading of them.

That is also why the adapter refuses to write graded prices until a human sets
``grade_fields_confirmed``. These fixtures cannot confirm it and neither can any
test written against them.
"""

from __future__ import annotations

BASE = "https://www.pricecharting.com"
PRODUCT_URL = f"{BASE}/api/product"
PRODUCTS_URL = f"{BASE}/api/products"

#: A card with the full ladder. Prices are integers in pennies, as documented.
UMBREON = {
    "status": "success",
    "id": "6910335",
    "product-name": "Umbreon VMAX (Alternate Art Secret) #215",
    "console-name": "Pokemon Evolving Skies",
    "release-date": "2021-08-27",
    "loose-price": 21500,
    "cib-price": 24000,
    "new-price": 27000,
    "graded-price": 30500,
    "box-only-price": 36000,
    "manual-only-price": 42000,
    "bgs-10-price": 96000,
}

#: Priced raw, and nothing above it. Very common: most cards are not graded
#: often enough for every rung to have sales behind it.
RAW_ONLY = {
    "status": "success",
    "id": "6910400",
    "product-name": "Vaporeon #130",
    "console-name": "Pokemon Evolving Skies",
    "release-date": "2021-08-27",
    "loose-price": 450,
    "graded-price": 0,
    "manual-only-price": 0,
}

#: In the guide, with no prices at all.
UNPRICED = {
    "status": "success",
    "id": "6910401",
    "product-name": "Basic Grass Energy",
    "console-name": "Pokemon Evolving Skies",
    "release-date": "2021-08-27",
}

SEARCH = {
    "status": "success",
    "products": [
        {
            "id": UMBREON["id"],
            "product-name": UMBREON["product-name"],
            "console-name": UMBREON["console-name"],
        },
        {
            "id": "6910336",
            "product-name": "Umbreon VMAX #95",
            "console-name": "Pokemon Evolving Skies",
        },
    ],
}

#: What a bad or expired token gets. The API answers 200 with a status field
#: rather than an HTTP error, so an adapter that only checks the status code
#: would read this as a card with no prices.
BAD_TOKEN = {"status": "error", "error-message": "Invalid token"}


def transport(product: dict | None = None, *, search: dict | None = None, **extra: object):
    """A RecordedTransport preloaded with a search and a product lookup."""
    from app.services.market_data.http import RecordedTransport

    responses: dict = {
        PRODUCTS_URL: search if search is not None else SEARCH,
        PRODUCT_URL: product if product is not None else UMBREON,
    }
    responses.update(extra)
    return RecordedTransport(responses=responses)
