"""Seed data.

Everything here is a *starting point the user owns*, not a fact the engine
depends on. Grading tiers, selling fees and grade rules are all rows precisely
because they change — see spec sections 9, 10, 22.

Pricing honesty rule: a tier is only seeded ``active`` when it carries a price
we can attribute. Tiers we have no verified price for are seeded **inactive**
with a zero price and a note, so the engine skips them rather than quietly
costing a submission at £0.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import DataSourceKind, GroupKind, Severity
from app.models import (
    CardSet,
    CardVariant,
    CollectionGroup,
    DataSource,
    GradeRule,
    GradingCompany,
    GradingMembership,
    GradingTier,
    SellingCostProfile,
)
from app.money import to_minor

SPEC_SOURCE_NOTE = (
    "Seeded from the product specification (August 2026). Grader pricing changes "
    "regularly — verify against the company's current price list before submitting."
)
UNPRICED_NOTE = (
    "No verified price seeded. Enter your current pricing and set the tier active "
    "to include it in the decision engine."
)

# --- Grading companies -------------------------------------------------------

COMPANIES: tuple[dict, ...] = (
    {
        "code": "PSA",
        "name": "Professional Sports Authenticator",
        "country": "US",
        "currency": "GBP",
        "website": "https://www.psacard.com",
        "market_recognition_score": 9.5,
        "supports_half_grades": False,
        "supports_subgrades": False,
        "sort_order": 10,
    },
    {
        "code": "CGC",
        "name": "CGC Cards",
        "country": "UK",
        "currency": "GBP",
        "website": "https://www.cgccards.com",
        "market_recognition_score": 7.5,
        "supports_half_grades": True,
        "supports_subgrades": True,
        "sort_order": 20,
    },
    {
        "code": "ACE",
        "name": "ACE Grading",
        "country": "UK",
        "currency": "GBP",
        "website": "https://acegrading.com",
        "market_recognition_score": 5.5,
        "supports_half_grades": True,
        "supports_subgrades": True,
        "sort_order": 30,
    },
    {
        "code": "BGS",
        "name": "Beckett Grading Services",
        "country": "US",
        "currency": "GBP",
        "website": "https://www.beckett.com",
        "market_recognition_score": 7.0,
        "supports_half_grades": True,
        "supports_subgrades": True,
        "active": False,
        "sort_order": 40,
    },
    {
        "code": "SGC",
        "name": "SGC",
        "country": "US",
        "currency": "GBP",
        "website": "https://gosgc.com",
        "market_recognition_score": 6.0,
        "supports_half_grades": True,
        "active": False,
        "sort_order": 50,
    },
)

# price / limits taken from the figures quoted in the product spec.
TIERS: dict[str, tuple[dict, ...]] = {
    "CGC": (
        {
            "tier_code": "bulk",
            "tier_name": "Bulk",
            "price": 16.80,
            "minimum_cards": 25,
            "max_declared_value": 400.00,
            "turnaround_days": 45,
            "sort_order": 10,
            "active": True,
        },
        {
            "tier_code": "economy",
            "tier_name": "Economy",
            "price": 19.00,
            "minimum_cards": 1,
            "max_declared_value": 800.00,
            "turnaround_days": 30,
            "sort_order": 20,
            "active": True,
        },
        {
            "tier_code": "standard",
            "tier_name": "Standard",
            "price": 54.00,
            "minimum_cards": 1,
            "max_declared_value": 2500.00,
            "turnaround_days": 20,
            "sort_order": 30,
            "active": True,
        },
    ),
    "ACE": (
        {
            "tier_code": "basic",
            "tier_name": "Basic",
            "price": 18.00,
            "minimum_cards": 1,
            "turnaround_days": 45,
            "sort_order": 10,
            "active": True,
        },
        {
            "tier_code": "standard",
            "tier_name": "Standard",
            "price": 25.00,
            "minimum_cards": 1,
            "turnaround_days": 30,
            "sort_order": 20,
            "active": True,
        },
        {
            "tier_code": "premier",
            "tier_name": "Premier",
            "price": 32.00,
            "minimum_cards": 1,
            "turnaround_days": 21,
            "sort_order": 30,
            "active": True,
        },
        {
            "tier_code": "ultra",
            "tier_name": "Ultra",
            "price": 60.00,
            "minimum_cards": 1,
            "turnaround_days": 14,
            "sort_order": 40,
            "active": True,
        },
        {
            "tier_code": "luxury",
            "tier_name": "Luxury",
            "price": 120.00,
            "minimum_cards": 1,
            "turnaround_days": 7,
            "sort_order": 50,
            "active": True,
        },
    ),
    # Structure only — the user supplies current prices.
    "PSA": (
        {
            "tier_code": "bulk",
            "tier_name": "Bulk",
            "price": 0.0,
            "minimum_cards": 20,
            "max_declared_value": 200.00,
            "sort_order": 10,
            "active": False,
        },
        {
            "tier_code": "value",
            "tier_name": "Value",
            "price": 0.0,
            "minimum_cards": 1,
            "max_declared_value": 500.00,
            "sort_order": 20,
            "active": False,
        },
        {
            "tier_code": "regular",
            "tier_name": "Regular",
            "price": 0.0,
            "minimum_cards": 1,
            "max_declared_value": 1500.00,
            "sort_order": 30,
            "active": False,
        },
        {
            "tier_code": "express",
            "tier_name": "Express",
            "price": 0.0,
            "minimum_cards": 1,
            "max_declared_value": 5000.00,
            "sort_order": 40,
            "active": False,
        },
    ),
}

MEMBERSHIPS: dict[str, tuple[dict, ...]] = {
    "CGC": (
        {
            "code": "cgc_premium",
            "name": "CGC Premium membership",
            "annual_fee": 0.0,
            "discount_pct": 0.0,
            "notes": "CGC offers member pricing. Enter your membership fee and discount to let "
            "the optimiser decide whether it pays for itself.",
        },
    ),
    "ACE": (
        {
            "code": "ace_member",
            "name": "ACE membership",
            "annual_fee": 0.0,
            "discount_pct": 0.0,
            "notes": "ACE lists separate membership-only pricing. Enter your fee and discount here.",
        },
    ),
}

# --- Selling cost profiles ---------------------------------------------------

SELLING_PROFILES: tuple[dict, ...] = (
    {
        "code": "ebay_uk",
        "name": "eBay UK",
        "platform": "ebay",
        "platform_fee_pct": 12.0,
        "payment_fee_pct": 0.0,
        "payment_fixed_fee": 0.30,
        "fees_apply_to_shipping": True,
        "shipping_charged_to_buyer": 1.55,
        "shipping_cost": 1.55,
        "packaging_cost": 0.35,
        "graded_shipping_cost": 5.50,
        "graded_packaging_cost": 1.20,
        "is_default": True,
        "sort_order": 10,
        "notes": "Default fee estimate — check your own eBay fee rate and update it.",
    },
    {
        "code": "cardmarket",
        "name": "Cardmarket",
        "platform": "cardmarket",
        "platform_fee_pct": 5.0,
        "payment_fee_pct": 0.0,
        "payment_fixed_fee": 0.0,
        "fees_apply_to_shipping": False,
        "shipping_charged_to_buyer": 2.00,
        "shipping_cost": 2.20,
        "packaging_cost": 0.35,
        "graded_shipping_cost": 6.50,
        "graded_packaging_cost": 1.20,
        "sort_order": 20,
        "notes": "Commission estimate — verify your current Cardmarket rate.",
    },
    {
        "code": "private",
        "name": "Private sale",
        "platform": "private",
        "platform_fee_pct": 0.0,
        "payment_fee_pct": 0.0,
        "payment_fixed_fee": 0.0,
        "fees_apply_to_shipping": False,
        "shipping_charged_to_buyer": 0.0,
        "shipping_cost": 0.0,
        "packaging_cost": 0.0,
        "sort_order": 30,
        "notes": "No fees. Usually means a lower achievable price — reflect that in the "
        "quick-sale discount.",
    },
)

# --- Market data sources (all network providers start disabled) --------------

#: Configuration this build shipped, later found to be wrong, and what replaces
#: it — applied only where the stored value still matches the old one exactly.
#:
#: Keyed by source code, each entry ``(superseded_grade_fields, corrections)``.
SUPERSEDED_CONFIG: dict[str, tuple[tuple[dict, dict], ...]] = {
    "pricecharting": (
        (
            # Shipped in the first PriceCharting release. Checking it against
            # the published guide showed graded-price and box-only-price are
            # the generic Grade 9 and Grade 9.5, and turned up four fields the
            # mapping had missed entirely.
            {
                "loose-price": "raw",
                "graded-price": "PSA 9",
                "box-only-price": "PSA 9.5",
                "manual-only-price": "PSA 10",
                "bgs-10-price": "BGS 10",
            },
            {
                "grade_fields": {
                    "loose-price": "raw",
                    "cib-price": "PSA 7",
                    "new-price": "PSA 8",
                    "graded-price": "PSA 9",
                    "box-only-price": "PSA 9.5",
                    "manual-only-price": "PSA 10",
                    "bgs-10-price": "BGS 10",
                    "condition-17-price": "CGC 10",
                    "condition-18-price": "SGC 10",
                },
                "grade_fields_confirmed": True,
            },
        ),
    ),
}


def _adopt_source(existing: DataSource, spec: dict) -> None:
    """Let an already-created source pick up work done since it was created.

    A row written before its adapter existed is inert forever otherwise: it has
    no ``provider_class``, so it cannot be enabled, so the feature simply never
    arrives for anyone who ran an earlier version. Upgrading a build has to
    upgrade the reference data with it.

    What it will not touch is ``enabled``, except in the one direction covered
    below. Turning a network source on because a new version shipped one would
    be making an outbound-traffic decision on the user's behalf.
    """
    if spec.get("provider_class") and not existing.provider_class:
        existing.provider_class = spec["provider_class"]
        # Config travels with the adapter: it is the adapter's settings, and a
        # marketplace or a window that the old row never had is not optional.
        existing.config = {**(spec.get("config") or {}), **(existing.config or {})}
        existing.rate_limit_per_minute = (
            existing.rate_limit_per_minute or spec.get("rate_limit_per_minute")
        )
        # The notes say what a source can and cannot do, and the old ones said
        # "no adapter written yet", which is now false.
        existing.notes = spec.get("notes") or existing.notes
        existing.terms_url = spec.get("terms_url") or existing.terms_url

    # A shipped default that turned out to be wrong has to be able to reach a
    # database that already has it. PriceCharting's mapping went out claiming
    # PSA 9 and PSA 9.5 for what are actually generic, grader-pooled grades;
    # leaving that in place would keep mislabelling every graded price on any
    # install that had already seeded it.
    #
    # Only where the stored mapping is *exactly* the superseded one. A mapping
    # the user has edited is their decision and is never overwritten — which is
    # the whole reason it lives in config.
    for superseded, corrected in SUPERSEDED_CONFIG.get(existing.code, ()):
        config = existing.config or {}
        if config.get("grade_fields") == superseded:
            existing.config = {**config, **corrected}

    # The one enable that is safe: a source that ships on by default, in a
    # database that predates that default, which the user has never run and so
    # has never deliberately turned off.
    if (
        spec.get("enabled")
        and not existing.enabled
        and existing.last_sync_at is None
        and existing.last_sync_status is None
    ):
        existing.enabled = True


DATA_SOURCES: tuple[dict, ...] = (
    {
        "code": "manual",
        "name": "Manual entry",
        "kind": DataSourceKind.MANUAL.value,
        "provider_class": "app.services.market_data.manual.ManualProvider",
        "enabled": True,
        "priority": 10,
        "notes": "Sales and prices you type in yourself. Always available, never expires.",
    },
    {
        "code": "csv",
        "name": "CSV import",
        "kind": DataSourceKind.CSV_IMPORT.value,
        "provider_class": "app.services.market_data.csv_import.CsvImportProvider",
        "enabled": True,
        "priority": 20,
        "notes": "Bulk import of sold listings exported from anywhere.",
    },
    {
        "code": "pokeprice",
        "name": "PokePrice",
        "kind": DataSourceKind.MARKET_DATA.value,
        "base_url": "https://www.pokeprice.io",
        "api_key_env_var": "SLABSTACK_POKEPRICE_API_KEY",
        "enabled": False,
        "priority": 30,
        "notes": "Needs a PokePrice account and API key. No adapter written yet.",
    },
    {
        "code": "pricecharting",
        "name": "PriceCharting",
        "kind": DataSourceKind.MARKET_DATA.value,
        "provider_class": "app.services.market_data.pricecharting.PriceChartingProvider",
        "base_url": "https://www.pricecharting.com",
        "api_key_env_var": "SLABSTACK_PRICECHARTING_API_KEY",
        "enabled": False,
        "priority": 40,
        "rate_limit_per_minute": 60,
        "config": {
            # Which JSON field holds which grade. PriceCharting is a video-game
            # price guide that also covers cards, and it reuses the game
            # condition fields for grades — "box-only" and "manual-only" hold
            # grades, not boxes and manuals.
            #
            # This is data rather than code because it is the one fact about
            # this source that most needs to be correctable without a release,
            # and because nobody has yet checked it against the site.
            "grade_fields": {
                "loose-price": "raw",
                # PriceCharting states grades generically below the tens —
                # "Grade 7", "Grade 9.5" — pooling every grader. Read as PSA,
                # which is an approximation and is said so in the notes below.
                "cib-price": "PSA 7",
                "new-price": "PSA 8",
                "graded-price": "PSA 9",
                "box-only-price": "PSA 9.5",
                # Company-specific, and exact.
                "manual-only-price": "PSA 10",
                "bgs-10-price": "BGS 10",
                "condition-17-price": "CGC 10",
                "condition-18-price": "SGC 10",
            },
            # Taken from PriceCharting's own "Description of Keys" table and
            # cross-checked against a real response. Check it yourself with:
            #   make pricecharting-fields CARD="Umbreon VMAX Evolving Skies"
            # Set this false and only the raw price is written, which is what a
            # source with an unverified mapping should do.
            "grade_fields_confirmed": True,
        },
        "terms_url": "https://www.pricecharting.com/api-documentation",
        "notes": (
            "Graded prices as named fields — the only source here that fills the slab side of "
            "the grading decision without an approval process. Needs a paid API key. Quotes "
            "USD, so an exchange rate is required. Aggregates, not individual sales, so "
            "liquidity stays unknown. "
            "Nine grades and no more: the API names the tens per company (PSA 10, BGS 10, "
            "CGC 10, SGC 10) and reports 7, 8, 9 and 9.5 generically across graders — those "
            "four are recorded as PSA, which is an approximation, not a reading. ACE 10 and "
            "TAG 10 are on the website but not in the API at any tier, so no ACE route can be "
            "priced from here. Run `make pricecharting-fields` to see it for yourself."
        ),
    },
    {
        "code": "ebay",
        "name": "eBay",
        "kind": DataSourceKind.MARKET_DATA.value,
        "provider_class": "app.services.market_data.ebay.EbayProvider",
        "base_url": "https://api.ebay.com",
        "api_key_env_var": "SLABSTACK_EBAY_APP_ID",
        # Off by default, unlike the catalogue source. That one can work with no
        # setup at all; this one cannot do anything without two credentials, and
        # a source switched on that immediately fails to authenticate is worse
        # than one the user turns on when they are ready.
        "enabled": False,
        "priority": 50,
        "rate_limit_per_minute": 30,
        "config": {
            # Decides the currency eBay quotes, so it decides whether an
            # exchange rate is needed at all. GB by default because the app
            # ships with GBP as its currency; change both together.
            "marketplace": "EBAY_GB",
            # The *name* of the variable holding the client secret. Never the
            # secret. eBay calls the pair App ID and Cert ID.
            "api_secret_env_var": "SLABSTACK_EBAY_CERT_ID",
            # eBay's own limit on sold data, stated rather than discovered.
            "sold_window_days": 90,
        },
        "terms_url": "https://developer.ebay.com/api-docs/static/ebay-rest-landing.html",
        "notes": (
            "Individual sold listings — the only source here that can fill the graded side of "
            "the grading decision, because slabs sell on eBay with the grade in the title. "
            "Also the only one that can measure liquidity, which needs real trades. "
            "Needs a developer application for SLABSTACK_EBAY_APP_ID and "
            "SLABSTACK_EBAY_CERT_ID. Sold data needs Marketplace Insights, which eBay "
            "documents as a limited release not open to new users — expect active listings "
            "only, which feed liquidity but carry no sold or graded prices. Official API "
            "under its terms, never scraped."
        ),
    },
    {
        "code": "cardmarket",
        "name": "Cardmarket",
        "kind": DataSourceKind.MARKET_DATA.value,
        "base_url": "https://api.cardmarket.com",
        "api_key_env_var": "SLABSTACK_CARDMARKET_TOKEN",
        "enabled": False,
        "priority": 60,
        "notes": "Needs a Cardmarket account and OAuth credentials. Official API only.",
    },
    {
        "code": "tcgplayer",
        "name": "TCGplayer",
        "kind": DataSourceKind.MARKET_DATA.value,
        "base_url": "https://api.tcgplayer.com",
        "api_key_env_var": "SLABSTACK_TCGPLAYER_TOKEN",
        "enabled": False,
        "priority": 70,
        "notes": "Needs partner approval from TCGplayer. Official API only.",
    },
    {
        "code": "pokemontcg_io",
        "name": "Pokémon TCG API",
        "kind": DataSourceKind.CARD_CATALOG.value,
        "provider_class": "app.services.market_data.pokemontcg.PokemonTcgIoProvider",
        "base_url": "https://api.pokemontcg.io/v2",
        "api_key_env_var": "SLABSTACK_POKEMONTCG_API_KEY",
        # On by default, because it is the only source that can work with no
        # setup at all: no key, no account, no approval. A switch the user has
        # to find before anything works is a worse default than an outbound
        # request they can turn off — and it is off in one click, in Settings.
        "enabled": True,
        "priority": 80,
        "rate_limit_per_minute": 20,
        "config": {
            # Works anonymously; a key raises the daily ceiling.
            "api_key_optional": True,
            "marketplace": "tcgplayer",
        },
        "terms_url": "https://pokemontcg.io/",
        "notes": (
            "Card catalogue plus aggregate TCGplayer/Cardmarket prices. No individual sales "
            "and no graded prices, so liquidity stays unknown and the grading decision still "
            "needs graded comparables from elsewhere."
        ),
    },
)

# --- Variants ----------------------------------------------------------------

VARIANTS: tuple[tuple[str, str, int], ...] = (
    ("standard", "Standard", 10),
    ("holo", "Holo", 20),
    ("reverse-holo", "Reverse Holo", 30),
    ("alt-art", "Alternate Art", 40),
    ("full-art", "Full Art", 50),
    ("illustration-rare", "Illustration Rare", 60),
    ("special-illustration-rare", "Special Illustration Rare", 70),
    ("secret-rare", "Secret Rare", 80),
    ("rainbow-rare", "Rainbow Rare", 90),
    ("gold", "Gold", 100),
    ("trainer-gallery", "Trainer Gallery", 110),
    ("jumbo", "Jumbo", 120),
    ("promo", "Promo", 130),
)

# --- Starter set catalogue ---------------------------------------------------
# Small on purpose. The real catalogue comes from a card-catalogue provider —
# enable one and look a card up, and its set, number and rarity arrive with it.
# These are enough for search and card entry to work with no source at all.
SETS: tuple[tuple[str, str, str, str | None], ...] = (
    ("BS", "Base Set", "Original Series", "1999-01-09"),
    ("EVS", "Evolving Skies", "Sword & Shield", "2021-08-27"),
    ("BRS", "Brilliant Stars", "Sword & Shield", "2022-02-25"),
    ("LOR", "Lost Origin", "Sword & Shield", "2022-09-09"),
    ("SIT", "Silver Tempest", "Sword & Shield", "2022-11-11"),
    ("CRZ", "Crown Zenith", "Sword & Shield", "2023-01-20"),
    ("OBF", "Obsidian Flames", "Scarlet & Violet", "2023-08-11"),
    ("MEW", "151", "Scarlet & Violet", "2023-09-22"),
    ("PAR", "Paradox Rift", "Scarlet & Violet", "2023-11-03"),
    ("PAF", "Paldean Fates", "Scarlet & Violet", "2024-01-26"),
    ("TWM", "Twilight Masquerade", "Scarlet & Violet", "2024-05-24"),
    ("SSP", "Surging Sparks", "Scarlet & Violet", "2024-11-08"),
    ("PRE", "Prismatic Evolutions", "Scarlet & Violet", "2025-01-17"),
)

# --- Grade rules (spec section 9) --------------------------------------------
# Explicitly OUR estimated model, not any grader's published standard.
GRADE_RULES: tuple[dict, ...] = (
    {
        "code": "crease_severe",
        "label": "Severe crease",
        "field": "creases",
        "min_severity": Severity.SEVERE.value,
        "max_grade": 3.0,
        "sort_order": 10,
    },
    {
        "code": "crease_moderate",
        "label": "Major crease",
        "field": "creases",
        "min_severity": Severity.MODERATE.value,
        "max_grade": 5.0,
        "sort_order": 20,
    },
    {
        "code": "crease_minor",
        "label": "Light crease or bend",
        "field": "creases",
        "min_severity": Severity.MINOR.value,
        "max_grade": 7.0,
        "sort_order": 30,
    },
    {
        "code": "dent_moderate",
        "label": "Visible dent",
        "field": "dents",
        "min_severity": Severity.MODERATE.value,
        "max_grade": 7.0,
        "sort_order": 40,
    },
    {
        "code": "whitening_severe",
        "label": "Heavy whitening",
        "field": "whitening",
        "min_severity": Severity.SEVERE.value,
        "max_grade": 8.0,
        "sort_order": 50,
    },
    {
        "code": "whitening_minor",
        "label": "Minor whitening",
        "field": "whitening",
        "min_severity": Severity.MINOR.value,
        "probability_multiplier": 0.75,
        "penalty_from_grade": 10.0,
        "sort_order": 60,
    },
    {
        "code": "corner_severe",
        "label": "Severe corner damage",
        "field": "corner_any",
        "min_severity": Severity.SEVERE.value,
        "max_grade": 6.0,
        "sort_order": 70,
    },
    {
        "code": "corner_moderate",
        "label": "Moderate corner wear",
        "field": "corner_any",
        "min_severity": Severity.MODERATE.value,
        "max_grade": 8.0,
        "sort_order": 80,
    },
    {
        "code": "surface_severe",
        "label": "Severe surface damage",
        "field": "surface_condition",
        "min_severity": Severity.SEVERE.value,
        "max_grade": 6.0,
        "sort_order": 90,
    },
    {
        "code": "scratches_minor",
        "label": "Surface scratches",
        "field": "scratches",
        "min_severity": Severity.MINOR.value,
        "probability_multiplier": 0.80,
        "penalty_from_grade": 10.0,
        "sort_order": 100,
    },
    {
        "code": "print_lines_moderate",
        "label": "Print lines",
        "field": "print_lines",
        "min_severity": Severity.MODERATE.value,
        "probability_multiplier": 0.60,
        "penalty_from_grade": 9.0,
        "sort_order": 110,
    },
    {
        "code": "silvering_moderate",
        "label": "Silvering",
        "field": "silvering",
        "min_severity": Severity.MODERATE.value,
        "probability_multiplier": 0.70,
        "penalty_from_grade": 9.0,
        "sort_order": 120,
    },
    {
        "code": "edge_severe",
        "label": "Severe edge wear",
        "field": "edge_condition",
        "min_severity": Severity.SEVERE.value,
        "max_grade": 7.0,
        "sort_order": 130,
    },
    {
        "code": "staining_moderate",
        "label": "Staining",
        "field": "staining",
        "min_severity": Severity.MODERATE.value,
        "max_grade": 6.0,
        "sort_order": 140,
    },
    {
        "code": "holo_severe",
        "label": "Severe holo damage",
        "field": "holo_condition",
        "min_severity": Severity.SEVERE.value,
        "max_grade": 8.0,
        "sort_order": 150,
    },
)


def _get_by(db: Session, model, **filters):
    stmt = select(model).filter_by(**filters)
    return db.scalars(stmt).first()


def seed_all(db: Session, *, force: bool = False) -> dict[str, int]:
    """Insert reference data that is missing. Idempotent.

    Existing rows are never overwritten unless ``force`` is set — the user's
    edited prices are more correct than our defaults.
    """
    counts = {
        "companies": 0,
        "tiers": 0,
        "memberships": 0,
        "selling_profiles": 0,
        "data_sources": 0,
        "variants": 0,
        "sets": 0,
        "grade_rules": 0,
        "groups": 0,
    }
    today = date.today()

    for spec in COMPANIES:
        company = _get_by(db, GradingCompany, code=spec["code"])
        if company is None:
            company = GradingCompany(**spec)
            db.add(company)
            db.flush()
            counts["companies"] += 1
        elif not force:
            continue

        for tier_spec in TIERS.get(spec["code"], ()):
            existing = _get_by(db, GradingTier, company_id=company.id, tier_code=tier_spec["tier_code"])
            if existing is not None:
                continue
            active = tier_spec.get("active", False)
            db.add(
                GradingTier(
                    company_id=company.id,
                    tier_code=tier_spec["tier_code"],
                    tier_name=tier_spec["tier_name"],
                    price_minor=to_minor(tier_spec["price"]) or 0,
                    currency=company.currency,
                    minimum_cards=tier_spec.get("minimum_cards", 1),
                    maximum_cards=tier_spec.get("maximum_cards"),
                    max_declared_value_minor=to_minor(tier_spec.get("max_declared_value")),
                    turnaround_days=tier_spec.get("turnaround_days"),
                    active=active,
                    sort_order=tier_spec.get("sort_order", 100),
                    source_url=company.website,
                    source_checked_at=today if active else None,
                    notes=SPEC_SOURCE_NOTE if active else UNPRICED_NOTE,
                )
            )
            counts["tiers"] += 1

        for membership_spec in MEMBERSHIPS.get(spec["code"], ()):
            existing = _get_by(db, GradingMembership, company_id=company.id, code=membership_spec["code"])
            if existing is not None:
                continue
            db.add(
                GradingMembership(
                    company_id=company.id,
                    code=membership_spec["code"],
                    name=membership_spec["name"],
                    annual_fee_minor=to_minor(membership_spec["annual_fee"]) or 0,
                    currency=company.currency,
                    discount_pct=membership_spec["discount_pct"],
                    notes=membership_spec.get("notes"),
                )
            )
            counts["memberships"] += 1

    for profile in SELLING_PROFILES:
        if _get_by(db, SellingCostProfile, code=profile["code"]) is not None:
            continue
        db.add(
            SellingCostProfile(
                code=profile["code"],
                name=profile["name"],
                platform=profile["platform"],
                platform_fee_pct=profile["platform_fee_pct"],
                payment_fee_pct=profile["payment_fee_pct"],
                payment_fixed_fee_minor=to_minor(profile["payment_fixed_fee"]) or 0,
                fees_apply_to_shipping=profile["fees_apply_to_shipping"],
                shipping_charged_to_buyer_minor=to_minor(profile["shipping_charged_to_buyer"]) or 0,
                shipping_cost_minor=to_minor(profile["shipping_cost"]) or 0,
                packaging_cost_minor=to_minor(profile["packaging_cost"]) or 0,
                graded_shipping_cost_minor=to_minor(profile.get("graded_shipping_cost")),
                graded_packaging_cost_minor=to_minor(profile.get("graded_packaging_cost")),
                is_default=profile.get("is_default", False),
                sort_order=profile.get("sort_order", 100),
                notes=profile.get("notes"),
            )
        )
        counts["selling_profiles"] += 1

    for source in DATA_SOURCES:
        existing = _get_by(db, DataSource, code=source["code"])
        if existing is not None:
            _adopt_source(existing, source)
            continue
        db.add(DataSource(**source))
        counts["data_sources"] += 1

    for code, name, sort_order in VARIANTS:
        if _get_by(db, CardVariant, code=code) is not None:
            continue
        db.add(CardVariant(code=code, name=name, sort_order=sort_order, is_builtin=True))
        counts["variants"] += 1

    for code, name, series, released in SETS:
        if _get_by(db, CardSet, code=code, language="English") is not None:
            continue
        db.add(
            CardSet(
                code=code,
                name=name,
                series=series,
                release_date=date.fromisoformat(released) if released else None,
            )
        )
        counts["sets"] += 1

    for rule in GRADE_RULES:
        if _get_by(db, GradeRule, code=rule["code"]) is not None:
            continue
        db.add(GradeRule(is_builtin=True, face="any", **rule))
        counts["grade_rules"] += 1

    if _get_by(db, CollectionGroup, name="Watchlist") is None:
        db.add(
            CollectionGroup(
                name="Watchlist",
                kind=GroupKind.WATCHLIST.value,
                description="Cards to keep an eye on.",
                color="#f59e0b",
                sort_order=10,
            )
        )
        counts["groups"] += 1

    db.flush()
    return counts
