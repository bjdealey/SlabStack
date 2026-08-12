"""The PriceCharting adapter, and its refusal to guess.

Most of these are ordinary parsing tests. The ones that matter are about a
single question the tests themselves cannot answer: *which JSON field holds
which grade?*

PriceCharting reuses its video-game condition fields for card grades, and this
build has never been able to reach the site to confirm the mapping. A wrong
mapping does not fail — it puts a real, plausible, correctly-denominated number
under the wrong grade and inverts the recommendation on every card it touches.

So the adapter treats the mapping as unconfirmed data, and what is tested here
is that it *behaves* like something unconfirmed: the raw price flows, the graded
prices do not, and both change the moment a human says the mapping is right.
"""

from __future__ import annotations

import pytest

from app.services.market_data.base import CardQuery, MarketKey
from app.services.market_data.http import ProviderRequestError
from app.services.market_data.pricecharting import PriceChartingProvider
from tests.fixtures import pricecharting as recorded

UMBREON = CardQuery(name="Umbreon VMAX", set_name="Evolving Skies", card_number="215/203")


def provider(*, confirmed: bool = False, transport=None, **config) -> PriceChartingProvider:
    return PriceChartingProvider(
        config={"grade_fields_confirmed": confirmed, **config},
        api_key="recorded-token",
        transport=transport or recorded.transport(),
    )


def key(grade_label: str = "raw", external_id: str = "2513024") -> MarketKey:
    return MarketKey(catalog_key="k", grade_label=grade_label, external_id=external_id)


# --- The mapping it will not guess -------------------------------------------


def test_the_raw_price_flows_without_confirmation():
    """"Loose" is the one label here that cannot mean anything else."""
    point = provider().get_current_price(key("raw"))
    assert point is not None
    assert point.value_minor == 220_000, "$2,200.00 — Ungraded on the site"
    assert point.currency == "USD"


def test_a_graded_price_is_withheld_until_the_mapping_is_confirmed():
    """The whole safety property.

    manual-only-price is $4,300 in the fixture. If the mapping is wrong that is
    some other grade's price wearing a PSA 10 label — a number no reader could
    tell from a correct one.
    """
    assert provider(confirmed=False).get_current_price(key("PSA 10")) is None


def test_confirming_the_mapping_releases_the_graded_prices():
    point = provider(confirmed=True).get_current_price(key("PSA 10"))
    assert point is not None
    assert point.value_minor == 430_000, "$4,300.00 — PSA 10 on the site"
    assert point.raw["field"] == "manual-only-price"


def test_the_graded_capability_reports_the_truth_about_itself():
    """The sync engine asks before it asks for data; it must not be lied to."""
    assert provider(confirmed=False).capabilities().graded_prices is False
    assert provider(confirmed=True).capabilities().graded_prices is True


def test_the_mapping_is_data_and_a_corrected_one_is_obeyed():
    """If the folklore is wrong, fixing it is a config edit, not a release."""
    corrected = provider(
        confirmed=True,
        grade_fields={"loose-price": "raw", "bgs-10-price": "PSA 10"},
    )
    point = corrected.get_current_price(key("PSA 10"))
    assert point is not None
    assert point.value_minor == 507_680, "read the field it was told to, not the default"


def test_a_mapping_naming_something_that_is_not_a_price_is_refused():
    with pytest.raises(ValueError, match="product-name"):
        PriceChartingProvider(
            config={"grade_fields": {"product-name": "PSA 10"}},
            api_key="x",
            transport=object(),
        )


# --- Reading the response ----------------------------------------------------


def test_prices_are_already_in_minor_units():
    """220000 is $2,200.00. Converting to float and back only loses pennies."""
    point = provider().get_current_price(key("raw"))
    assert point.value_minor == 220_000


def test_a_zero_price_is_an_absence_not_a_price_of_nothing():
    """Every card has some grade with too few sales behind it."""
    adapter = provider(confirmed=True, transport=recorded.transport(recorded.RAW_ONLY))
    assert adapter.get_current_price(key("PSA 10", "2513100")) is None
    assert adapter.get_current_price(key("raw", "2513100")).value_minor == 450


def test_a_product_with_no_prices_returns_nothing():
    adapter = provider(transport=recorded.transport(recorded.UNPRICED))
    assert adapter.get_current_price(key("raw", "2513101")) is None


def test_the_sample_size_stays_zero():
    """An aggregate is not evidence of sales you could have made."""
    assert provider().get_current_price(key("raw")).sample_size == 0


def test_the_price_carries_no_date_rather_than_the_release_date():
    """The response dates the card, not the price. Using it would misdate today."""
    assert provider().get_current_price(key("raw")).as_of is None


def test_an_error_status_is_raised_even_though_the_http_status_was_200():
    """A bad token answers 200 with a status field, not an HTTP error.

    An adapter checking only the status code would read this as a card with no
    prices, and report "no data" for every card forever.
    """
    adapter = provider(transport=recorded.transport(recorded.BAD_TOKEN))
    with pytest.raises(ProviderRequestError, match="Invalid token"):
        adapter.get_current_price(key("raw"))


def test_a_missing_token_says_there_is_no_free_tier():
    adapter = PriceChartingProvider(config={}, api_key=None, transport=recorded.transport())
    with pytest.raises(ProviderRequestError, match="paid subscription"):
        adapter.get_current_price(key("raw"))


def test_the_token_travels_as_a_query_parameter():
    transport = recorded.transport()
    provider(transport=transport).get_current_price(key("raw"))
    _url, params, _headers = transport.calls[0]
    assert params["t"] == "recorded-token"


def test_an_unlinked_card_is_not_searched_for_on_every_sync():
    """Matching by name each time would drift onto a different printing."""
    assert provider().get_current_price(MarketKey(catalog_key="k")) is None


# --- Search ------------------------------------------------------------------


def test_search_returns_candidates_with_their_set():
    matches = provider().search_card(UMBREON)
    assert matches
    assert matches[0].external_id == "2513024"
    assert matches[0].set_name == "Pokemon Evolving Skies"


def test_the_query_carries_the_set_because_a_bare_name_matches_everything():
    transport = recorded.transport()
    provider(transport=transport).search_card(UMBREON)
    _url, params, _headers = transport.calls[0]
    assert "Umbreon VMAX" in params["q"]
    assert "Evolving Skies" in params["q"]


def test_it_offers_search_but_needs_the_link_stored():
    caps = provider().capabilities()
    assert caps.search is True
    assert caps.current_price is True
    assert provider().requires_external_id is True


def test_it_claims_no_sales_or_listings():
    """Aggregates only. Claiming otherwise would have the sync ask for nothing."""
    caps = provider().capabilities()
    assert caps.sales_history is False
    assert caps.active_listings is False


def test_the_raw_fields_are_exposed_unmapped_for_confirmation():
    """`make pricecharting-fields` shows what arrived, not a tidied version."""
    fields = provider().fields_for_product("2513024")
    assert fields["manual-only-price"] == 430_000
    assert fields["box-only-price"] == 290_000
    assert fields["condition-17-price"] == 286_515, "including the ones once missed"


# --- The sync, which is where this source nearly failed silently -------------


@pytest.fixture
def pc_source(db):
    """PriceCharting, enabled, with a key present for the duration."""
    import os

    from sqlalchemy import select

    from app.models import DataSource

    os.environ["SLABSTACK_PRICECHARTING_API_KEY"] = "recorded-token"
    source = db.scalar(select(DataSource).where(DataSource.code == "pricecharting"))
    source.enabled = True
    db.commit()
    yield source
    os.environ.pop("SLABSTACK_PRICECHARTING_API_KEY", None)


def run_sync(db, source, monkeypatch, *, confirmed: bool):
    from app.services import market_sync

    adapter = provider(confirmed=confirmed)
    monkeypatch.setattr(market_sync, "load_provider", lambda _source: adapter)
    return market_sync.sync_source(db, source)


def linked_card(client, db, source) -> str:
    from app.models import Card

    # PriceCharting quotes USD and this app reports GBP, so without a rate every
    # price is fetched and deliberately not written. Exactly the state a new
    # install is in, and the first thing to check when nothing appears.
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.79}}})

    card = client.post(
        "/api/cards",
        json={"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"},
    ).json()
    row = db.get(Card, card["id"])
    row.external_ids = {"pricecharting": "2513024"}
    db.commit()
    return card["id"]


def stored_labels(db, card_id) -> set[str]:
    from sqlalchemy import select

    from app.models import Card, MarketPrice

    key = db.get(Card, card_id).catalog_key
    return {
        row.grade_label
        for row in db.scalars(select(MarketPrice).where(MarketPrice.catalog_key == key))
    }


def test_the_sync_asks_for_every_grade_not_just_raw(db, client, pc_source, monkeypatch):
    """The bug this source was shipped with.

    The price sync hardcoded grade_label="raw", which was harmless while no
    provider had graded prices and made a graded-price source unable to deliver
    a single one the moment it existed. Everything else was correct — the
    adapter, the mapping, the key — and nothing appeared.
    """
    card_id = linked_card(client, db, pc_source)
    run_sync(db, pc_source, monkeypatch, confirmed=True)

    assert stored_labels(db, card_id) >= {
        "raw", "PSA 7", "PSA 8", "PSA 9", "PSA 9.5", "PSA 10", "BGS 10", "CGC 10", "SGC 10"
    }


def test_an_unconfirmed_mapping_writes_only_the_raw_price(db, client, pc_source, monkeypatch):
    card_id = linked_card(client, db, pc_source)
    run_sync(db, pc_source, monkeypatch, confirmed=False)

    assert stored_labels(db, card_id) == {"raw"}


def test_a_graded_row_carries_its_company(db, client, pc_source, monkeypatch):
    """Every engine downstream costs a route within one grader.

    A PSA 10 price with no company attached is invisible to all of it — the
    best-case route is computed strictly per company, because pairing ACE's fee
    with PSA's slab price describes a route that does not exist.
    """
    from sqlalchemy import select

    from app.models import Card, GradingCompany, MarketPrice

    card_id = linked_card(client, db, pc_source)
    run_sync(db, pc_source, monkeypatch, confirmed=True)

    key = db.get(Card, card_id).catalog_key
    row = db.scalars(
        select(MarketPrice).where(
            MarketPrice.catalog_key == key, MarketPrice.grade_label == "PSA 10"
        )
    ).first()
    psa = db.scalars(select(GradingCompany).where(GradingCompany.code == "PSA")).first()

    assert row.grade == 10.0
    assert row.company_id == psa.id


def test_the_report_names_the_grades_it_wrote(db, client, pc_source, monkeypatch):
    linked_card(client, db, pc_source)
    report = run_sync(db, pc_source, monkeypatch, confirmed=True)

    assert report.cards[0].status == "updated"
    assert "PSA 10" in report.cards[0].grades


def test_without_an_exchange_rate_nothing_is_written_and_the_run_says_why(
    db, client, pc_source, monkeypatch
):
    """The likeliest reason a correctly configured source appears to do nothing.

    Every step can be right — key set, source enabled, card linked, mapping
    confirmed — and still no price appears, because PriceCharting quotes USD and
    this app reports one currency. Guessing a rate would rescale every price
    silently, so the run fetches, refuses, and explains.
    """
    from app.models import Card

    card = client.post(
        "/api/cards", json={"name": "Umbreon VMAX", "card_number": "215/203"}
    ).json()
    db.get(Card, card["id"]).external_ids = {"pricecharting": "2513024"}
    db.commit()

    report = run_sync(db, pc_source, monkeypatch, confirmed=True)

    assert stored_labels(db, card["id"]) == set(), "fetched, and deliberately not written"
    assert "USD→GBP rate" in (report.cards[0].reason or "")


# --- Correcting a mapping already sitting in somebody's database -------------


def test_the_superseded_mapping_is_corrected_in_an_existing_database(db, client):
    """A wrong default has to be able to reach a database that already has it.

    The first PriceCharting release claimed PSA 9 and PSA 9.5 for what are
    actually generic, grader-pooled grades. Anyone who had already seeded it
    would go on mislabelling every graded price, and nothing would say so.
    """
    from sqlalchemy import select

    from app.models import DataSource
    from app.services import seed

    source = db.scalar(select(DataSource).where(DataSource.code == "pricecharting"))
    source.config = {
        "grade_fields": {
            "loose-price": "raw",
            "graded-price": "PSA 9",
            "box-only-price": "PSA 9.5",
            "manual-only-price": "PSA 10",
            "bgs-10-price": "BGS 10",
        },
        "grade_fields_confirmed": False,
    }
    db.commit()

    seed.seed_all(db)
    db.commit()
    db.refresh(source)

    assert source.config["grade_fields"]["graded-price"] == "PSA 9"
    assert source.config["grade_fields"]["condition-17-price"] == "CGC 10"
    assert source.config["grade_fields_confirmed"] is True


def test_a_mapping_you_edited_is_never_overwritten(db, client):
    """The reason it lives in config at all. Your correction outranks the default."""
    from sqlalchemy import select

    from app.models import DataSource
    from app.services import seed

    source = db.scalar(select(DataSource).where(DataSource.code == "pricecharting"))
    mine = {"loose-price": "raw", "bgs-10-price": "PSA 10"}
    source.config = {"grade_fields": mine, "grade_fields_confirmed": True}
    db.commit()

    seed.seed_all(db)
    db.commit()
    db.refresh(source)

    assert source.config["grade_fields"] == mine


def test_the_documented_key_table_is_the_whole_ladder(db, client):
    """Nine grades, and the tenth does not exist to be had.

    PriceCharting's "Description of Keys" table is the complete list of what
    /api/product and the CSV return. ACE 10 and TAG 10 are on the website and
    are not in it at any subscription tier — so an ACE route, the cheapest
    grader this app supports, can never be priced from this source. Better to
    have that asserted here than rediscovered as a puzzling blank.
    """
    from sqlalchemy import select

    from app.models import DataSource

    source = db.scalar(select(DataSource).where(DataSource.code == "pricecharting"))
    labels = set(source.config["grade_fields"].values())

    assert labels == {
        "raw", "PSA 7", "PSA 8", "PSA 9", "PSA 9.5", "PSA 10", "BGS 10", "CGC 10", "SGC 10"
    }
    assert not any(label.startswith(("ACE", "TAG")) for label in labels)


def test_the_rate_limit_leaves_room_under_one_call_a_second():
    """Exceeding it is not throttled — the documentation says revoked."""
    from app.services.market_data import pricecharting as module

    assert module.DEFAULT_RATE_LIMIT < 60


# --- Yearly volume, which is what makes liquidity knowable -------------------


def test_the_yearly_sales_volume_is_read():
    """Not a price and not a sample size: how often the card trades."""
    point = provider().get_current_price(key("raw"))
    assert point.annual_volume == 312
    assert point.sample_size == 0, "still zero — this price rests on no sales of yours"


def test_a_product_with_no_volume_reports_none_rather_than_zero():
    """None is "the source did not say"; zero would be "nothing sold all year"."""
    adapter = provider(transport=recorded.transport(recorded.RAW_ONLY))
    assert adapter.get_current_price(key("raw", "2513100")).annual_volume is None


def test_the_volume_reaches_liquidity_through_a_sync(db, client, pc_source, monkeypatch):
    """End to end: the API's count becomes a liquidity score on the card.

    Liquidity had read "unknown" on every card in this build, from every source,
    while being one of the five components of the decision score.
    """
    from app.models import Card
    from app.services import market_service

    card_id = linked_card(client, db, pc_source)
    run_sync(db, pc_source, monkeypatch, confirmed=True)
    db.commit()

    key_value = db.get(Card, card_id).catalog_key
    summary = market_service.summarise(
        db, key_value, params=market_service.MarketParameters(), currency="GBP"
    )

    assert summary.liquidity.annual_volume == 312
    assert summary.liquidity.basis == "reported_volume"
    assert summary.liquidity.score is not None
    assert summary.liquidity.band != "unknown"


def test_the_card_page_says_the_score_came_from_a_source_not_your_sales(
    db, client, pc_source, monkeypatch
):
    """A score with no sales behind it must not read like one that has them."""
    card_id = linked_card(client, db, pc_source)
    run_sync(db, pc_source, monkeypatch, confirmed=True)
    db.commit()

    block = client.get(f"/api/cards/{card_id}/evaluation").json()["liquidity"]

    assert block["basis"] == "reported_volume"
    assert block["annual_volume"] == 312
    assert block["status"] == "partial"
    assert "recently" in block["reason"], "the half it cannot answer is named"
    assert "0 sale(s)" not in (block["reason"] or ""), "never 'based on 0 sales'"
