"""The adapter and the sync engine, tested without a network.

What these can prove: that a documented response is parsed correctly, that the
awkward cases are handled, and that nothing is written when a number cannot be
stated honestly. What they cannot prove: that the live API still returns this
shape. That is verified on first run, which is why the adapter is written to
fail loudly rather than return a half-populated row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Card, DataSource, MarketPrice, PriceSnapshot
from app.services import market_sync
from app.services.market_data.base import CardQuery, MarketKey
from app.services.market_data.http import ProviderRequestError, RecordedTransport
from app.services.market_data.pokemontcg import PokemonTcgIoProvider
from tests.fixtures import pokemontcg as fx


def provider(**config) -> PokemonTcgIoProvider:
    return PokemonTcgIoProvider(config=config, transport=fx.transport())


# --- Parsing -----------------------------------------------------------------


def test_a_documented_response_yields_the_price_it_advertises():
    point = provider().price_for_external_id("swsh7-215", variant="Alternate Art")
    assert point is not None
    # TCGplayer `market` is its own average of recent sales — the closest thing
    # it has to "what it actually goes for".
    assert point.value_minor == 41_055
    assert point.currency == "USD"
    assert point.as_of.isoformat() == "2026-08-10"
    assert point.raw["field"] == "holofoil.market"


def test_the_sample_size_is_zero_and_must_stay_zero():
    """It is an index, not a count of sales, and confidence keys off it."""
    point = provider().price_for_external_id("swsh7-215")
    assert point.sample_size == 0


def test_the_variant_picks_the_printing():
    """A reverse holo and a normal are different markets."""
    normal = provider().price_for_external_id("swsh45-25", variant=None)
    reverse = provider().price_for_external_id("swsh45-25", variant="reverse-holo")
    assert normal.value_minor == 94
    assert reverse.value_minor == 311


def test_a_card_with_no_price_block_returns_nothing_rather_than_zero():
    assert provider().price_for_external_id("base1-4") is None


def test_an_all_zero_price_block_reads_as_no_price():
    """£0.00 is a claim about the market. Absence is not."""
    assert provider().price_for_external_id("swsh1-1") is None


def test_cardmarket_can_be_chosen_and_quotes_euros():
    point = provider(marketplace="cardmarket").price_for_external_id("swsh7-215")
    assert point.currency == "EUR"
    assert point.value_minor == 36_250
    assert point.raw["field"] == "trendPrice"


def test_an_unknown_marketplace_is_refused_at_construction():
    with pytest.raises(ValueError, match="not a marketplace"):
        PokemonTcgIoProvider(config={"marketplace": "ebay"}, transport=fx.transport())


def test_a_graded_key_gets_no_price_at_all():
    """The most damaging thing this adapter could do is answer this one.

    There are no graded prices at this source. Returning the raw price for a
    graded key would put the same number on both sides of the grading decision
    and make grading look exactly break-even, every single time.
    """
    key = MarketKey(
        catalog_key="english|evs|215|alternate-art|unlimited",
        grade_label="PSA 10",
        external_id="swsh7-215",
    )
    assert provider().get_current_price(key) is None


def test_the_adapter_declares_what_it_cannot_do():
    caps = provider().capabilities()
    assert caps.search and caps.current_price
    assert not caps.sales_history, "no individual sales, so liquidity stays unknown"
    assert not caps.graded_prices, "no slab prices, so the grading decision needs elsewhere"


# --- Search ------------------------------------------------------------------


def test_search_builds_a_narrow_query_from_what_is_known():
    transport = fx.transport()
    instance = PokemonTcgIoProvider(transport=transport)
    instance.search_card(
        CardQuery(name="Umbreon VMAX", set_code="EVS", card_number="215/203", limit=5)
    )

    _, params, _ = transport.calls[0]
    assert 'name:"Umbreon VMAX"' in params["q"]
    assert 'number:"215"' in params["q"], "the numerator, not the printed 215/203"
    assert "EVS" in params["q"]


def test_a_match_carries_the_printed_card_number():
    matches = provider().search_card(CardQuery(name="Umbreon VMAX"))
    assert matches[0].card_number == "215/203"
    assert matches[0].set_code == "EVS"
    assert matches[0].image_url


def test_confidence_rewards_the_fields_that_pin_a_card_down():
    """A name alone is weak — there are a dozen Pikachus."""
    loose = provider().search_card(CardQuery(name="Umbreon VMAX"))[0]
    tight = provider().search_card(
        CardQuery(name="Umbreon VMAX", set_code="EVS", card_number="215/203")
    )[0]
    assert tight.confidence > loose.confidence


def test_an_empty_query_asks_the_provider_nothing():
    transport = fx.transport()
    instance = PokemonTcgIoProvider(transport=transport)
    assert instance.search_card(CardQuery()) == []
    assert not transport.calls, "no query means no request, not a request for everything"


def test_the_api_key_is_sent_when_there_is_one():
    transport = fx.transport()
    PokemonTcgIoProvider(api_key="abc123", transport=transport).search_card(
        CardQuery(name="Umbreon VMAX")
    )
    assert transport.calls[0][2]["X-Api-Key"] == "abc123"


def test_it_works_without_a_key():
    transport = fx.transport()
    PokemonTcgIoProvider(transport=transport).search_card(CardQuery(name="Umbreon VMAX"))
    assert "X-Api-Key" not in transport.calls[0][2]


def test_an_unrecorded_url_raises_rather_than_returning_nothing():
    """A silently empty response is how a broken adapter passes its tests."""
    instance = PokemonTcgIoProvider(transport=RecordedTransport(responses={}))
    with pytest.raises(ProviderRequestError):
        instance.price_for_external_id("swsh7-215")


# --- Currency ----------------------------------------------------------------


def test_no_rate_means_no_number():
    """A guessed rate rescales every price silently. Better to write nothing."""
    value, rate = market_sync.convert_minor(
        10_000, from_currency="USD", to_currency="GBP", rates={}
    )
    assert value is None and rate is None


def test_a_configured_rate_is_used_and_reported():
    value, rate = market_sync.convert_minor(
        10_000, from_currency="USD", to_currency="GBP", rates={"USD_GBP": 0.79}
    )
    assert value == 7_900
    assert rate == 0.79


def test_the_inverse_rate_is_accepted():
    """Someone who set GBP_USD should not have to also set USD_GBP."""
    value, rate = market_sync.convert_minor(
        10_000, from_currency="USD", to_currency="GBP", rates={"GBP_USD": 1.25}
    )
    assert value == 8_000
    assert rate == 0.8


def test_same_currency_needs_no_rate():
    value, rate = market_sync.convert_minor(
        10_000, from_currency="GBP", to_currency="GBP", rates={}
    )
    assert value == 10_000 and rate == 1.0


def test_a_nonsense_rate_is_ignored_rather_than_applied():
    for bad in ({"USD_GBP": 0}, {"USD_GBP": -1}, {"USD_GBP": "cheap"}):
        value, _ = market_sync.convert_minor(
            10_000, from_currency="USD", to_currency="GBP", rates=bad
        )
        assert value is None


# --- Sync --------------------------------------------------------------------


@pytest.fixture
def linked_card(client: TestClient, db) -> Card:
    """A card the user has confirmed against the catalogue."""
    created = client.post(
        "/api/cards",
        json={"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"},
    ).json()
    card = db.get(Card, created["id"])
    card.external_ids = {"pokemontcg_io": "swsh7-215"}
    db.commit()
    return card


def enable_source(db, *, code: str = "pokemontcg_io") -> DataSource:
    source = db.scalar(select(DataSource).where(DataSource.code == code))
    source.enabled = True
    db.commit()
    return source


def run_sync(db, monkeypatch, *, source: DataSource):
    """Sync with the recorded transport standing in for the network."""
    monkeypatch.setattr(
        "app.services.market_sync.load_provider",
        lambda _source: PokemonTcgIoProvider(
            config=_source.config or {}, transport=fx.transport()
        ),
    )
    return market_sync.sync_source(db, source)


def test_a_sync_writes_the_providers_own_row(client, db, monkeypatch, linked_card):
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.8}}})
    source = enable_source(db)

    report = run_sync(db, monkeypatch, source=source)
    assert report.updated == 1
    outcome = report.cards[0]
    assert outcome.source_value == 410.55
    assert outcome.source_currency == "USD"
    assert outcome.fx_rate == 0.8
    assert outcome.value == 328.44

    row = db.scalar(select(MarketPrice).where(MarketPrice.source_id == source.id))
    assert row.median_minor == 32_844
    assert row.sample_size == 0, "an index is not evidence of sales"
    assert row.low_quartile_minor is None, "no spread is invented from one number"


def test_a_missing_rate_fetches_but_writes_nothing(client, db, monkeypatch, linked_card):
    """The price is real; stating it in the wrong currency would not be."""
    source = enable_source(db)
    report = run_sync(db, monkeypatch, source=source)

    assert report.updated == 0
    assert report.cards[0].source_value == 410.55, "it was fetched"
    assert "no USD→GBP rate is set" in report.cards[0].reason
    assert db.scalar(select(MarketPrice).where(MarketPrice.source_id == source.id)) is None
    assert any("Settings → Market" in note for note in report.notes)


def test_a_sync_snapshots_so_a_trend_can_accrue(client, db, monkeypatch, linked_card):
    """This source has no history to import, so the only trend is the one built."""
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.8}}})
    source = enable_source(db)
    run_sync(db, monkeypatch, source=source)

    snapshots = list(db.scalars(select(PriceSnapshot).where(PriceSnapshot.source_id == source.id)))
    assert len(snapshots) == 1
    assert snapshots[0].value_minor == 32_844


def test_syncing_twice_in_a_day_is_not_two_data_points(client, db, monkeypatch, linked_card):
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.8}}})
    source = enable_source(db)
    run_sync(db, monkeypatch, source=source)
    run_sync(db, monkeypatch, source=source)

    snapshots = list(db.scalars(select(PriceSnapshot).where(PriceSnapshot.source_id == source.id)))
    assert len(snapshots) == 1


def test_an_unlinked_card_is_skipped_with_a_reason(client, db, monkeypatch):
    client.post("/api/cards", json={"name": "Unlinked", "set_code": "EVS", "card_number": "1/1"})
    source = enable_source(db)

    report = run_sync(db, monkeypatch, source=source)
    assert report.status == "insufficient_data"
    assert "no card is linked to this source" in (report.reason or "").lower()


def test_a_disabled_source_refuses_and_says_so(client, db, monkeypatch, linked_card):
    source = db.scalar(select(DataSource).where(DataSource.code == "pokemontcg_io"))
    report = market_sync.sync_source(db, source)
    assert report.status == "error"
    assert "disabled" in report.reason


def test_a_provider_failure_leaves_existing_prices_alone(client, db, monkeypatch, linked_card):
    """A sync that breaks must cost future updates, never history."""
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.8}}})
    source = enable_source(db)
    run_sync(db, monkeypatch, source=source)
    before = db.scalar(select(MarketPrice).where(MarketPrice.source_id == source.id)).median_minor

    monkeypatch.setattr(
        "app.services.market_sync.load_provider",
        lambda _source: PokemonTcgIoProvider(transport=RecordedTransport(responses={})),
    )
    report = market_sync.sync_source(db, source)

    assert report.failed == 1
    after = db.scalar(select(MarketPrice).where(MarketPrice.source_id == source.id))
    assert after.median_minor == before, "the old price survived the failed run"


def test_the_outcome_is_recorded_on_the_source(client, db, monkeypatch, linked_card):
    """So the UI can show the last run without triggering one."""
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.8}}})
    source = enable_source(db)
    run_sync(db, monkeypatch, source=source)

    db.refresh(source)
    assert source.last_sync_at is not None
    assert source.last_sync_status == "ok"
    assert source.last_sync_error is None


def test_your_own_sales_still_win_after_a_sync(client, db, monkeypatch, linked_card):
    """The whole precedence rule, end to end."""
    client.patch("/api/settings", json={"values": {"fx_rates": {"USD_GBP": 0.8}}})
    for index in range(12):
        client.post(
            f"/api/cards/{linked_card.id}/market/sales",
            json={"sale_date": "2026-08-01", "sale_price": 150 + index},
        )
    source = enable_source(db)
    run_sync(db, monkeypatch, source=source)

    market = client.get(f"/api/cards/{linked_card.id}/evaluation").json()["market"]
    assert market["raw"]["source_code"] is None, "your sales, not the index"
    assert market["raw"]["sample_size"] == 12
