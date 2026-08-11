"""Parsing, exclusion heuristics and the import path.

The heuristics get the most attention here, because they are the part that can
silently throw away a real comparable. Both directions matter: the filter has to
catch job lots and Japanese prints, and it has to leave a plain listing alone.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.enums import SaleExclusionReason
from app.models import Card, MarketSale
from app.services import market_service, sales_import
from app.services.sales_import import SaleContext

TODAY = date(2026, 6, 1)

ENGLISH_ALT = SaleContext(
    catalog_key="english|evs|215-203|alternate-art|unlimited",
    language="English",
    variant="Alternate Art",
)


def verdict(title: str, *, context: SaleContext = ENGLISH_ALT, **kwargs) -> str | None:
    result = sales_import.classify(title=title, context=context, **kwargs)
    return result[0] if result else None


# --- Money and dates ---------------------------------------------------------


def test_prices_parse_however_they_were_exported():
    assert sales_import.parse_money("£1,234.56") == 123456
    assert sales_import.parse_money("1.234,56") == 123456
    assert sales_import.parse_money("$45.00") == 4500
    assert sales_import.parse_money("45") == 4500
    assert sales_import.parse_money("12,50") == 1250
    assert sales_import.parse_money(18.8) == 1880
    assert sales_import.parse_money("GBP 99.99") == 9999


def test_unparseable_prices_are_none_not_zero():
    assert sales_import.parse_money("") is None
    assert sales_import.parse_money("free") is None
    assert sales_import.parse_money(None) is None


def test_dates_parse_in_the_formats_exports_actually_use():
    assert sales_import.parse_date("2025-03-04") == date(2025, 3, 4)
    assert sales_import.parse_date("4 Mar 2025") == date(2025, 3, 4)
    assert sales_import.parse_date("2025-03-04T14:22:01Z") == date(2025, 3, 4)
    assert sales_import.parse_date("04/03/2025") == date(2025, 3, 4)
    assert sales_import.parse_date("03/04/2025", day_first=False) == date(2025, 3, 4)
    assert sales_import.parse_date("nonsense") is None


def test_grades_are_read_out_of_listing_titles():
    assert sales_import.parse_grade_from_title("Umbreon VMAX Alt Art PSA 10 GEM") == ("PSA", 10.0)
    assert sales_import.parse_grade_from_title("Charizard CGC 9.5") == ("CGC", 9.5)
    assert sales_import.parse_grade_from_title("Blastoise BECKETT 8") == ("BGS", 8.0)
    assert sales_import.parse_grade_from_title("Pikachu NM raw") is None


# --- Exclusion heuristics ----------------------------------------------------


def test_a_plain_listing_is_left_alone():
    assert verdict("Pokemon Umbreon VMAX Alternate Art 215/203 Evolving Skies NM") is None


def test_lots_and_bundles_are_excluded():
    assert verdict("Pokemon Job Lot 50 Cards") == SaleExclusionReason.LOT_OR_BUNDLE.value
    assert verdict("Umbreon VMAX Alt Art bundle") == SaleExclusionReason.LOT_OR_BUNDLE.value
    assert verdict("Evolving Skies Elite Trainer Box") == SaleExclusionReason.LOT_OR_BUNDLE.value


def test_a_multi_card_listing_is_excluded_on_its_lot_size_alone():
    """No title needed: a price for four cards is not a price for one."""
    assert verdict("", lot_size=4) == SaleExclusionReason.LOT_OR_BUNDLE.value


def test_damage_is_excluded():
    assert verdict("Umbreon VMAX Alt Art creased corner") == SaleExclusionReason.DAMAGED.value
    assert verdict("Umbreon VMAX Alt Art heavily played") == SaleExclusionReason.DAMAGED.value


def test_customs_and_proxies_are_excluded():
    assert verdict("Custom Umbreon VMAX metal card") == SaleExclusionReason.SUSPECTED_FAKE.value
    assert verdict("Umbreon VMAX ORICA proxy") == SaleExclusionReason.SUSPECTED_FAKE.value


def test_a_wrong_language_comparable_is_excluded():
    assert verdict("Japanese Umbreon VMAX Alt Art") == SaleExclusionReason.WRONG_LANGUAGE.value
    assert verdict("Umbreon VMAX Alt Art Korean") == SaleExclusionReason.WRONG_LANGUAGE.value


def test_silence_about_language_is_not_evidence_of_the_wrong_one():
    """Most English listings never say 'English'. Excluding them would gut the sample."""
    assert verdict("Umbreon VMAX Alternate Art 215/203") is None


def test_an_ambiguous_multi_language_title_is_not_excluded():
    assert verdict("Umbreon VMAX Alt Art English or Japanese available") is None


def test_a_wrong_variant_comparable_is_excluded():
    assert verdict("Umbreon VMAX Reverse Holo") == SaleExclusionReason.WRONG_VARIANT.value


def test_the_printing_field_counts_as_an_accepted_variant():
    """A 1st edition comparable for a 1st edition card is not a mismatch."""
    context = SaleContext(
        catalog_key="k", language="English", variant="standard", printing="1st Edition"
    )
    assert verdict("Charizard 1st Edition Base Set Holo", context=context) is None
    assert (
        verdict("Charizard Full Art", context=context) == SaleExclusionReason.WRONG_VARIANT.value
    )


def test_a_graded_sale_does_not_count_as_a_raw_comparable():
    assert verdict("Umbreon VMAX Alt Art PSA 10", grade_label="raw") == (
        SaleExclusionReason.WRONG_GRADE.value
    )


def test_the_same_slab_counts_for_its_own_grade():
    assert verdict("Umbreon VMAX Alt Art PSA 10", grade_label="PSA 10") is None


def test_a_different_slab_is_excluded():
    assert verdict("Umbreon VMAX Alt Art PSA 9", grade_label="PSA 10") == (
        SaleExclusionReason.WRONG_GRADE.value
    )


def test_best_offer_sales_are_excluded_because_the_price_shown_is_not_the_price_paid():
    assert verdict("Umbreon VMAX Alt Art - Best Offer Accepted") == (
        SaleExclusionReason.BEST_OFFER_UNKNOWN.value
    )


# --- CSV ---------------------------------------------------------------------

CSV = """Date Sold,Sold For,Shipping,Title,Item ID,Platform
2026-05-20,£152.00,3.95,Umbreon VMAX Alt Art 215/203,111,eBay
2026-05-02,£148.50,3.95,Umbreon VMAX Alt Art 215/203 NM,112,eBay
2026-04-18,£160.00,0,Pokemon Job Lot 40 Cards,113,eBay
2026-04-02,£155.00,3.95,Japanese Umbreon VMAX Alt Art,114,eBay
"""


def test_csv_columns_are_matched_loosely():
    rows, errors = sales_import.parse_csv(CSV)
    assert errors == []
    assert len(rows) == 4
    assert rows[0].sale_date == date(2026, 5, 20)
    assert rows[0].sale_price_minor == 15200
    assert rows[0].shipping_minor == 395
    assert rows[0].external_id == "111"
    assert rows[0].platform == "eBay"


def test_csv_without_a_date_or_price_column_is_refused_with_a_useful_message():
    rows, errors = sales_import.parse_csv("Name,Colour\nfoo,red\n")
    assert rows == []
    assert errors and "sale_date" in errors[0].message


def test_bad_rows_are_reported_by_line_and_the_rest_still_import():
    text = "date,price\n2026-05-01,100\nnot-a-date,50\n2026-05-03,abc\n2026-05-04,20\n"
    rows, errors = sales_import.parse_csv(text)
    assert len(rows) == 2
    assert [error.line_number for error in errors] == [3, 4]


def test_a_semicolon_export_parses_too():
    rows, errors = sales_import.parse_csv("date;price;title\n2026-05-01;100,50;Umbreon\n")
    assert errors == []
    assert rows[0].sale_price_minor == 10050


# --- Import against the database ---------------------------------------------


def _card(db) -> Card:
    card = Card(
        name="Umbreon VMAX",
        catalog_key=ENGLISH_ALT.catalog_key,
        language="English",
        variant="Alternate Art",
    )
    db.add(card)
    db.flush()
    return card


def test_import_excludes_the_junk_and_keeps_it_visible(seeded_db):
    card = _card(seeded_db)
    rows, _ = sales_import.parse_csv(CSV)
    report = sales_import.import_rows(
        seeded_db, rows, context=ENGLISH_ALT, card_id=card.id, source_code="csv"
    )

    assert report.imported == 4
    assert report.excluded == 2
    assert report.exclusions == {
        SaleExclusionReason.LOT_OR_BUNDLE.value: 1,
        SaleExclusionReason.WRONG_LANGUAGE.value: 1,
    }

    stored = seeded_db.query(MarketSale).all()
    assert len(stored) == 4, "excluded sales are kept, never deleted"
    assert all(row.excluded_by == "system" for row in stored if row.is_excluded)


def test_reimporting_the_same_export_updates_rather_than_doubles(seeded_db):
    card = _card(seeded_db)
    rows, _ = sales_import.parse_csv(CSV)
    sales_import.import_rows(seeded_db, rows, context=ENGLISH_ALT, card_id=card.id)
    second = sales_import.import_rows(seeded_db, rows, context=ENGLISH_ALT, card_id=card.id)

    assert second.imported == 0
    assert second.updated == 4
    assert seeded_db.query(MarketSale).count() == 4


def test_a_user_decision_survives_a_reimport(seeded_db):
    card = _card(seeded_db)
    rows, _ = sales_import.parse_csv(CSV)
    sales_import.import_rows(seeded_db, rows, context=ENGLISH_ALT, card_id=card.id)

    lot = seeded_db.query(MarketSale).filter(MarketSale.external_id == "113").one()
    sales_import.set_exclusion(seeded_db, lot, excluded=False)

    sales_import.import_rows(seeded_db, rows, context=ENGLISH_ALT, card_id=card.id)
    seeded_db.refresh(lot)
    assert lot.is_excluded is False
    assert lot.excluded_by == "user"


def test_reclassification_leaves_user_decisions_alone(seeded_db):
    card = _card(seeded_db)
    rows, _ = sales_import.parse_csv(CSV)
    sales_import.import_rows(seeded_db, rows, context=ENGLISH_ALT, card_id=card.id)

    japanese = seeded_db.query(MarketSale).filter(MarketSale.external_id == "114").one()
    sales_import.set_exclusion(seeded_db, japanese, excluded=False)

    counts = sales_import.reclassify_key(seeded_db, context=ENGLISH_ALT)
    assert counts["skipped_user"] == 1
    seeded_db.refresh(japanese)
    assert japanese.is_excluded is False


def test_editing_the_card_language_changes_what_is_comparable(seeded_db):
    card = _card(seeded_db)
    rows, _ = sales_import.parse_csv(CSV)
    sales_import.import_rows(seeded_db, rows, context=ENGLISH_ALT, card_id=card.id)

    japanese_context = SaleContext(
        catalog_key=ENGLISH_ALT.catalog_key, language="Japanese", variant="Alternate Art"
    )
    sales_import.reclassify_key(seeded_db, context=japanese_context)

    japanese = seeded_db.query(MarketSale).filter(MarketSale.external_id == "114").one()
    assert japanese.is_excluded is False, "the Japanese sale is now the comparable one"


# --- Outliers ----------------------------------------------------------------


def _add_sales(db, prices: list[float], *, label: str = "raw") -> None:
    for index, price in enumerate(prices):
        db.add(
            MarketSale(
                catalog_key=ENGLISH_ALT.catalog_key,
                grade_label=label,
                sale_date=TODAY - timedelta(days=index * 3),
                sale_price_minor=int(price * 100),
                currency="GBP",
            )
        )
    db.flush()


def test_outliers_need_a_sample_before_the_fence_is_drawn(seeded_db):
    _add_sales(seeded_db, [100, 105, 110, 9000])
    counts = sales_import.mark_outliers(
        seeded_db, ENGLISH_ALT.catalog_key, params=market_service.MarketParameters()
    )
    assert counts["flagged"] == 0, "four sales cannot judge which of them is absurd"


def test_an_absurd_price_is_fenced_off_once_there_is_a_sample(seeded_db):
    _add_sales(seeded_db, [100, 102, 98, 105, 101, 99, 103, 97, 9000])
    counts = sales_import.mark_outliers(
        seeded_db, ENGLISH_ALT.catalog_key, params=market_service.MarketParameters()
    )
    assert counts["flagged"] == 1

    flagged = seeded_db.query(MarketSale).filter(MarketSale.is_outlier.is_(True)).one()
    assert flagged.sale_price_minor == 900_000
    assert flagged.exclusion_reason == SaleExclusionReason.PRICE_OUTLIER.value


def test_outliers_are_judged_per_grade_not_across_grades(seeded_db):
    """A PSA 10 at ten times the raw price is the normal state of affairs."""
    _add_sales(seeded_db, [100, 102, 98, 105, 101, 99, 103, 97])
    _add_sales(seeded_db, [1000, 1020, 980, 1050, 1010, 990, 1030, 970], label="PSA 10")

    counts = sales_import.mark_outliers(
        seeded_db, ENGLISH_ALT.catalog_key, params=market_service.MarketParameters()
    )
    assert counts["flagged"] == 0


def test_an_outlier_is_cleared_when_it_stops_being_one(seeded_db):
    params = market_service.MarketParameters()
    _add_sales(seeded_db, [100, 102, 98, 105, 101, 99, 103, 97, 900])
    assert sales_import.mark_outliers(seeded_db, ENGLISH_ALT.catalog_key, params=params)["flagged"] == 1

    # The market catches up with the outlier.
    _add_sales(seeded_db, [700, 750, 800, 850, 880, 820, 780, 760])
    counts = sales_import.mark_outliers(seeded_db, ENGLISH_ALT.catalog_key, params=params)
    assert counts["cleared"] == 1
    assert seeded_db.query(MarketSale).filter(MarketSale.is_outlier.is_(True)).count() == 0
