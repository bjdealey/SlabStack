r"""Recorded PriceCharting responses — a real product, and a verified mapping.

These numbers are not invented. They are the response for Umbreon VMAX #215
(Pokemon Evolving Skies), and every field was matched against PriceCharting's
own published price guide for the same card by exact price. That is what turned
the field-to-grade mapping from folklore into something known:

    loose-price         $2,200.00   Ungraded
    cib-price           $1,700.00   Grade 7     \  generic on PriceCharting,
    new-price           $1,855.00   Grade 8      >  pooled across graders,
    graded-price        $2,180.00   Grade 9     /   recorded as PSA
    box-only-price      $2,900.00   Grade 9.5  /
    manual-only-price   $4,300.00   PSA 10      \
    bgs-10-price        $5,076.80   BGS 10       >  company-specific, exact
    condition-17-price  $2,865.15   CGC 10      /
    condition-18-price  $1,136.00   SGC 10     /

The check caught two errors in the widely repeated mapping: graded-price and
box-only-price are the generic Grade 9 and Grade 9.5, not PSA 9 and PSA 9.5.

Shown on the site but absent from the API response: TAG 10, ACE 10, BGS 10
Black, CGC 10 Pristine and Grades 1-6. ACE is a grader this app supports and
cannot price from here, which is worth knowing before trusting a route to it.
"""

from __future__ import annotations

BASE = "https://www.pricecharting.com"
PRODUCT_URL = f"{BASE}/api/product"
PRODUCTS_URL = f"{BASE}/api/products"

#: The real response. Prices are integers in pennies, as documented.
UMBREON = {
    "status": "success",
    "id": "2513024",
    "product-name": "Umbreon VMAX #215",
    "console-name": "Pokemon Evolving Skies",
    "release-date": "2021-08-27",
    "loose-price": 220000,
    "cib-price": 170000,
    "new-price": 185500,
    "graded-price": 218000,
    "box-only-price": 290000,
    "manual-only-price": 430000,
    "bgs-10-price": 507680,
    "condition-17-price": 286515,
    "condition-18-price": 113600,
}

#: Priced raw, and nothing above it. Very common: most cards are not graded
#: often enough for every rung to have sales behind it.
RAW_ONLY = {
    "status": "success",
    "id": "2513100",
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
    "id": "2513101",
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
            "id": "2512907",
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
