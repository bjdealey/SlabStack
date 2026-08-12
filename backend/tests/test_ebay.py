"""The eBay adapter, and the sales-level sync it feeds.

Two things are being tested and they are worth keeping apart in your head.

The **adapter** tests are about reading somebody else's JSON: does a sale come
out with the right price, date and title, and do the unusable rows get dropped
rather than turned into zeroes. Those are ordinary parsing tests.

The **sync** tests are about what happens to a page of real marketplace results
once it lands — which is the part with teeth. A single query returns raw sales,
graded sales, a job lot, a Japanese copy and a raw card advertised as "PSA 10
READY", and the whole value of this source depends on those ending up in the
right places. Getting it wrong produces a confident, plausible, wrong number.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Card, DataSource, MarketListing, MarketPrice, MarketSale
from app.services import market_sync
from app.services.market_data.base import CardQuery, MarketKey
from app.services.market_data.ebay import EbayProvider
from app.services.market_data.http import (
    CapabilityDeniedError,
    ProviderRequestError,
    RecordedTransport,
)
from tests.fixtures import ebay as recorded

UMBREON = CardQuery(name="Umbreon VMAX", card_number="215/203", set_code="EVS")


def provider(**kwargs) -> EbayProvider:
    transport = kwargs.pop("transport", None) or recorded.transport()
    config = {"marketplace": "EBAY_GB", "api_secret_env_var": "TEST_EBAY_SECRET"}
    config.update(kwargs.pop("config", {}))
    os.environ.setdefault("TEST_EBAY_SECRET", "recorded-secret")
    return EbayProvider(config=config, api_key="recorded-app-id", transport=transport)


# --- The adapter -------------------------------------------------------------


def test_it_reads_a_sold_listing():
    sales = provider().sales_for_query(UMBREON)
    psa = next(sale for sale in sales if "PSA 10" in (sale.listing_title or ""))

    assert psa.price_minor == 42_000
    assert psa.currency == "GBP"
    assert psa.sale_date == date(2026, 7, 14)
    assert psa.external_id == "v1|294001|0"
    assert psa.seller == "cardshop_uk"
    assert psa.platform == "eBay"


def test_rows_it_cannot_use_are_dropped_not_zeroed():
    """A sale with no price, no date, or a zero price is absent, not £0.00."""
    sales = provider().sales_for_query(UMBREON)
    ids = {sale.external_id for sale in sales}

    assert "v1|294008|0" not in ids, "no price"
    assert "v1|294009|0" not in ids, "no date"
    assert "v1|294010|0" not in ids, "zero price is not a price"
    assert all(sale.price_minor > 0 for sale in sales)


def test_the_token_is_fetched_once_and_reused():
    """A sync over two hundred cards must not be four hundred requests."""
    transport = recorded.transport()
    adapter = provider(transport=transport)
    adapter.sales_for_query(UMBREON)
    adapter.sales_for_query(UMBREON)

    tokens = [call for call in transport.calls if call[0] == recorded.TOKEN_URL]
    assert len(tokens) == 1


def test_the_token_request_sends_client_credentials():
    transport = recorded.transport()
    provider(transport=transport).sales_for_query(UMBREON)

    url, data, headers = transport.calls[0]
    assert url == recorded.TOKEN_URL
    assert data["grant_type"] == "client_credentials"
    assert headers["Authorization"].startswith("Basic ")
    # Application credentials, not a user's. Nothing here can act on an account.
    assert "api_scope" in data["scope"]


def test_a_missing_secret_says_which_half_is_missing():
    adapter = EbayProvider(
        config={"api_secret_env_var": "DEFINITELY_NOT_SET_ANYWHERE"},
        api_key="recorded-app-id",
        transport=recorded.transport(),
    )
    with pytest.raises(ProviderRequestError, match="DEFINITELY_NOT_SET_ANYWHERE"):
        adapter.sales_for_query(UMBREON)


def test_unapproved_sold_access_is_its_own_kind_of_failure():
    """403 from Insights means "apply for it", not "this card never sells"."""
    denied = ProviderRequestError("nope", status_code=403)
    adapter = provider(transport=recorded.transport(sold=denied))

    with pytest.raises(CapabilityDeniedError, match="Marketplace Insights"):
        adapter.sales_for_query(UMBREON)


def test_active_listings_carry_what_the_source_says_exists():
    """Three came back; eBay says sixty-one exist. Liquidity needs the second."""
    listings = provider().listings_for_query(UMBREON)
    assert len(listings) == 3
    assert listings[0].raw["result_total"] == 61


def test_a_marketplace_is_not_a_card_catalogue():
    """Claiming search would answer "what is this card" with "things for sale"."""
    caps = provider().capabilities()
    assert caps.search is False
    assert caps.current_price is False
    assert caps.sales_history is True
    assert caps.graded_prices is True


def test_it_needs_no_link_because_it_has_no_card_ids():
    assert provider().requires_external_id is False


def test_being_asked_for_a_card_it_was_not_told_is_loud():
    """An empty list here would read as "never sold", which is a lie."""
    with pytest.raises(ProviderRequestError, match="no card to search for"):
        provider().get_sales_history(MarketKey(catalog_key="k"))


def test_an_unknown_marketplace_is_refused_at_construction():
    with pytest.raises(ValueError, match="EBAY_ATLANTIS"):
        EbayProvider(config={"marketplace": "EBAY_ATLANTIS"}, api_key="x", transport=object())


def test_the_search_never_names_a_grade():
    """One broad query is what lets raw and graded sales arrive comparable."""
    transport = recorded.transport()
    provider(transport=transport).sales_for_query(UMBREON)
    terms = next(call for call in transport.calls if call[0] == recorded.SOLD_URL)[1]["q"]

    assert "Umbreon VMAX" in terms
    assert "215/203" in terms
    assert "PSA" not in terms.upper()


# --- The sync ----------------------------------------------------------------


@pytest.fixture
def ebay_source(db) -> DataSource:
    """eBay, switched on, with credentials present for the duration."""
    os.environ["SLABSTACK_EBAY_APP_ID"] = "recorded-app-id"
    os.environ["SLABSTACK_EBAY_CERT_ID"] = "recorded-secret"
    source = db.scalar(select(DataSource).where(DataSource.code == "ebay"))
    source.enabled = True
    db.commit()
    yield source
    for name in ("SLABSTACK_EBAY_APP_ID", "SLABSTACK_EBAY_CERT_ID"):
        os.environ.pop(name, None)


@pytest.fixture
def card(client: TestClient) -> dict:
    return client.post(
        "/api/cards",
        json={
            "name": "Umbreon VMAX",
            "set_code": "EVS",
            "card_number": "215/203",
            "variant": "Alternate Art",
            "language": "English",
        },
    ).json()


def run_sync(db, source, monkeypatch, *, transport=None) -> market_sync.SyncReport:
    """Sync eBay with recorded responses behind it."""
    adapter = EbayProvider(
        config={"marketplace": "EBAY_GB", "api_secret_env_var": "SLABSTACK_EBAY_CERT_ID"},
        api_key="recorded-app-id",
        transport=transport or recorded.transport(),
    )
    monkeypatch.setattr(market_sync, "load_provider", lambda _source: adapter)
    return market_sync.sync_source(db, source)


def test_one_query_fills_the_raw_price_and_the_graded_ladder(
    db, client, card, ebay_source, monkeypatch
):
    """The reason this source was worth building.

    Raw and graded prices come out of a single search, which means the two
    numbers the grading decision compares are finally both real.
    """
    report = run_sync(db, ebay_source, monkeypatch)
    assert report.status in {"ok", "partial"}

    key = db.get(Card, card["id"]).catalog_key
    labels = {
        row.grade_label
        for row in db.scalars(select(MarketPrice).where(MarketPrice.catalog_key == key))
    }
    assert "raw" in labels
    assert "PSA 10" in labels, "the graded side of the comparison"
    assert "CGC 9.5" in labels


def test_a_hoped_for_grade_lands_as_a_raw_sale_not_a_psa_10(
    db, client, card, ebay_source, monkeypatch
):
    """The single most damaging thing this source could get wrong.

    "PSA 10 READY" at £230 counted as a PSA 10 would sit beside a real PSA 10 at
    £420 and pull the graded price down by nearly half — turning a card worth
    grading into one that is not, with no sign anything went wrong.
    """
    run_sync(db, ebay_source, monkeypatch)
    key = db.get(Card, card["id"]).catalog_key

    aspirational = db.scalar(
        select(MarketSale).where(
            MarketSale.catalog_key == key, MarketSale.external_id == "v1|294004|0"
        )
    )
    assert aspirational is not None, "it is still a real sale and is kept"
    assert aspirational.grade_label == "raw"
    assert aspirational.is_excluded is False

    psa_sales = list(
        db.scalars(
            select(MarketSale).where(
                MarketSale.catalog_key == key,
                MarketSale.grade_label == "PSA 10",
                MarketSale.is_excluded.is_(False),
            )
        )
    )
    assert [sale.sale_price_minor for sale in psa_sales] == [42_000]


def test_the_rubbish_in_a_marketplace_search_is_excluded_and_kept(
    db, client, card, ebay_source, monkeypatch
):
    """Excluded is not deleted — every one can be inspected and reversed."""
    report = run_sync(db, ebay_source, monkeypatch)
    key = db.get(Card, card["id"]).catalog_key

    excluded = {
        row.external_id: row.exclusion_reason
        for row in db.scalars(
            select(MarketSale).where(
                MarketSale.catalog_key == key, MarketSale.is_excluded.is_(True)
            )
        )
    }
    assert excluded["v1|294005|0"] == "lot_or_bundle"
    assert excluded["v1|294006|0"] == "wrong_language"
    assert excluded["v1|294007|0"] == "wrong_variant"
    assert report.sales_excluded >= 3


def test_active_listings_are_recorded_but_never_as_sales(
    db, client, card, ebay_source, monkeypatch
):
    """An asking price is not a sale, and confusing the two inflates everything."""
    run_sync(db, ebay_source, monkeypatch)
    key = db.get(Card, card["id"]).catalog_key

    listings = list(db.scalars(select(MarketListing).where(MarketListing.catalog_key == key)))
    assert len(listings) == 3
    assert all(row.is_active for row in listings)

    asking = {row.price_minor for row in listings}
    sold = {
        row.sale_price_minor
        for row in db.scalars(select(MarketSale).where(MarketSale.catalog_key == key))
    }
    assert not (asking & sold), "no asking price leaked into the sales table"


def test_a_listing_that_has_gone_is_marked_inactive_not_deleted(
    db, client, card, ebay_source, monkeypatch
):
    """Otherwise the active count only ever grows and liquidity rots."""
    run_sync(db, ebay_source, monkeypatch)
    remaining = recorded.browse_response(recorded.ACTIVE[0], total=1)
    run_sync(db, ebay_source, monkeypatch, transport=recorded.transport(browse=remaining))

    key = db.get(Card, card["id"]).catalog_key
    rows = {
        row.external_id: row.is_active
        for row in db.scalars(select(MarketListing).where(MarketListing.catalog_key == key))
    }
    assert rows["v1|395001|0"] is True
    assert rows["v1|395002|0"] is False
    assert rows["v1|395003|0"] is False
    assert len(rows) == 3, "gone is not deleted"


def test_syncing_twice_does_not_double_the_sample(db, client, card, ebay_source, monkeypatch):
    """Deduplicated on (source, external_id), or every refresh inflates confidence."""
    first = run_sync(db, ebay_source, monkeypatch)
    key = db.get(Card, card["id"]).catalog_key
    after_one = db.scalars(select(MarketSale).where(MarketSale.catalog_key == key)).all()

    second = run_sync(db, ebay_source, monkeypatch)
    after_two = db.scalars(select(MarketSale).where(MarketSale.catalog_key == key)).all()

    assert len(after_one) == len(after_two)
    assert first.sales_imported == second.sales_imported


def test_an_unapproved_application_still_gets_its_listings(
    db, client, card, ebay_source, monkeypatch
):
    """The common state for a new developer account, and it must not read as broken."""
    denied = ProviderRequestError("nope", status_code=403)
    report = run_sync(
        db, ebay_source, monkeypatch, transport=recorded.transport(sold=denied)
    )

    key = db.get(Card, card["id"]).catalog_key
    assert db.scalars(select(MarketSale).where(MarketSale.catalog_key == key)).all() == []
    assert db.scalars(select(MarketListing).where(MarketListing.catalog_key == key)).all()
    assert any("Marketplace Insights" in note for note in report.notes)
    assert report.status != "error", "a source doing half of what it can is not a failure"


def test_a_card_needs_no_catalogue_link_for_a_marketplace(
    db, client, card, ebay_source, monkeypatch
):
    """A marketplace is searched by name. Requiring a link would sync nothing, ever."""
    assert not (db.get(Card, card["id"]).external_ids or {}).get("ebay")
    report = run_sync(db, ebay_source, monkeypatch)
    assert report.requested == 1
    assert report.sales_imported > 0


def test_foreign_sales_are_fetched_and_not_written_without_a_rate(
    db, client, card, ebay_source, monkeypatch
):
    """The same refusal as the price path: a guessed rate rescales a whole sample."""
    usd = recorded.sold_response(
        {**recorded.PSA_10, "lastSoldPrice": {"value": "500.00", "currency": "USD"}}
    )
    report = run_sync(db, ebay_source, monkeypatch, transport=recorded.transport(sold=usd))

    key = db.get(Card, card["id"]).catalog_key
    assert db.scalars(select(MarketSale).where(MarketSale.catalog_key == key)).all() == []
    assert "USD" in (report.cards[0].reason or "")


def test_the_run_reports_what_it_actually_did(db, client, card, ebay_source, monkeypatch):
    report = run_sync(db, ebay_source, monkeypatch)
    outcome = report.cards[0]

    assert outcome.sales_imported > 0
    assert outcome.sales_excluded >= 3
    assert outcome.listings_seen == 3
    assert outcome.listings_reported == 61, "the market is bigger than the page"
    assert "raw" in outcome.grades
    assert "PSA 10" in outcome.grades


def test_the_declared_value_does_not_deny_the_graded_sales_it_just_imported(
    db, client, card, ebay_source, monkeypatch
):
    """Found by driving the UI, not by a unit test.

    With PSA 10 and CGC 9.5 comparables on screen, the declared value still read
    "no graded sales are stored for this card". The number was right — an
    unassessed card has no grade distribution to weight with — but the reason
    was a false claim about the data, and it would send you off to import
    comparables you already had.
    """
    run_sync(db, ebay_source, monkeypatch)
    # The sync flushes; the route reads in its own session, as it does in the
    # real app where the endpoint commits before anything reads back.
    db.commit()

    evaluation = client.get(f"/api/cards/{card['id']}/evaluation").json()
    basis = next(
        item["detail"]
        for item in evaluation["explanation"]
        if item["text"].startswith("Declared value")
    )

    assert "no graded sales are stored" not in basis
    assert "PSA 10" in basis, "it should name what it does have"
    assert "assessed" in basis, "and the thing actually missing"


def test_a_capped_run_says_it_was_capped(db, client, ebay_source, monkeypatch):
    """A run that covered 1 of 3 must not read like one that covered everything."""
    for number in ("215/203", "216/203", "217/203"):
        client.post("/api/cards", json={"name": "Umbreon VMAX", "card_number": number})

    adapter = EbayProvider(
        config={"api_secret_env_var": "SLABSTACK_EBAY_CERT_ID"},
        api_key="recorded-app-id",
        transport=recorded.transport(),
    )
    monkeypatch.setattr(market_sync, "load_provider", lambda _source: adapter)
    report = market_sync.sync_source(db, ebay_source, limit=1)

    assert report.requested == 1
    assert any("3 card(s) could be synced" in note for note in report.notes)


def test_a_transport_with_no_recording_fails_rather_than_returning_nothing(
    db, client, card, ebay_source, monkeypatch
):
    """A silently empty response is how a broken adapter passes its tests."""
    report = run_sync(
        db, ebay_source, monkeypatch, transport=RecordedTransport(responses={})
    )
    assert report.status == "error" or report.failed == 1
