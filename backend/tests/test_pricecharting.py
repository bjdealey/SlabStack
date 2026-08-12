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


def key(grade_label: str = "raw", external_id: str = "6910335") -> MarketKey:
    return MarketKey(catalog_key="k", grade_label=grade_label, external_id=external_id)


# --- The mapping it will not guess -------------------------------------------


def test_the_raw_price_flows_without_confirmation():
    """"Loose" is the one label here that cannot mean anything else."""
    point = provider().get_current_price(key("raw"))
    assert point is not None
    assert point.value_minor == 21_500
    assert point.currency == "USD"


def test_a_graded_price_is_withheld_until_the_mapping_is_confirmed():
    """The whole safety property.

    manual-only-price is $420 in the fixture. If the mapping is wrong that is
    some other grade's price wearing a PSA 10 label — a number no reader could
    tell from a correct one.
    """
    assert provider(confirmed=False).get_current_price(key("PSA 10")) is None


def test_confirming_the_mapping_releases_the_graded_prices():
    point = provider(confirmed=True).get_current_price(key("PSA 10"))
    assert point is not None
    assert point.value_minor == 42_000
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
    assert point.value_minor == 96_000, "read the field it was told to, not the default"


def test_a_mapping_naming_something_that_is_not_a_price_is_refused():
    with pytest.raises(ValueError, match="product-name"):
        PriceChartingProvider(
            config={"grade_fields": {"product-name": "PSA 10"}},
            api_key="x",
            transport=object(),
        )


# --- Reading the response ----------------------------------------------------


def test_prices_are_already_in_minor_units():
    """21500 is $215.00. Converting to float and back only loses pennies."""
    point = provider().get_current_price(key("raw"))
    assert point.value_minor == 21_500


def test_a_zero_price_is_an_absence_not_a_price_of_nothing():
    """Every card has some grade with too few sales behind it."""
    adapter = provider(confirmed=True, transport=recorded.transport(recorded.RAW_ONLY))
    assert adapter.get_current_price(key("PSA 10", "6910400")) is None
    assert adapter.get_current_price(key("raw", "6910400")).value_minor == 450


def test_a_product_with_no_prices_returns_nothing():
    adapter = provider(transport=recorded.transport(recorded.UNPRICED))
    assert adapter.get_current_price(key("raw", "6910401")) is None


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
    assert matches[0].external_id == "6910335"
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
    fields = provider().fields_for_product("6910335")
    assert fields["manual-only-price"] == 42_000
    assert fields["box-only-price"] == 36_000
    assert fields["cib-price"] == 24_000, "including fields the mapping ignores"
