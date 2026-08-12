"""Reading back what is currently on sale.

Listings have been fetched and stored since the eBay adapter shipped; this is
the endpoint that finally shows them. Everything tested here is about not
letting an asking price pass for a sale, or one grade's market pass for
another's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models import MarketListing


@pytest.fixture
def card(client) -> dict:
    return client.post(
        "/api/cards",
        json={"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"},
    ).json()


def add_listing(db, key: str, *, price: int, grade: str = "raw", **fields) -> MarketListing:
    row = MarketListing(
        catalog_key=key, grade_label=grade, price_minor=price, currency="GBP", **fields
    )
    db.add(row)
    db.commit()
    return row


def test_listings_come_back_grouped_by_grade_not_interleaved_by_price(db, client, card):
    """A slabbed 10 and a raw copy are different markets.

    Ordered by price alone they interleave, and a reader averaging down the
    column gets a number that describes neither — the same error as the
    pooled-grade trend.
    """
    key = card["catalog_key"]
    add_listing(db, key, price=90_00, grade="PSA 10")
    add_listing(db, key, price=40_00, grade="raw")
    add_listing(db, key, price=120_00, grade="PSA 10")
    add_listing(db, key, price=45_00, grade="raw")

    rows = client.get(f"/api/cards/{card['id']}/market/listings").json()

    assert [row["grade_label"] for row in rows] == ["PSA 10", "PSA 10", "raw", "raw"]
    assert [row["price"] for row in rows] == [90.0, 120.0, 40.0, 45.0], "cheapest first within each"


def test_unknown_postage_is_not_free_postage(db, client, card):
    """A cheap card with expensive postage is not a cheap card.

    ``total_ask`` exists so the UI never has to add a number to a null, and it
    is absent rather than equal to the price when postage was not stated.
    """
    add_listing(db, card["catalog_key"], price=40_00)
    add_listing(db, card["catalog_key"], price=41_00, shipping_minor=3_50)

    unstated, stated = client.get(f"/api/cards/{card['id']}/market/listings").json()

    assert unstated["shipping"] is None
    assert unstated["total_ask"] is None, "not 40.00 — nobody said postage was free"
    assert stated["shipping"] == 3.5
    assert stated["total_ask"] == 44.5


def test_free_postage_is_stated_as_zero_not_as_silence(db, client, card):
    add_listing(db, card["catalog_key"], price=40_00, shipping_minor=0)

    (row,) = client.get(f"/api/cards/{card['id']}/market/listings").json()

    assert row["shipping"] == 0.0
    assert row["total_ask"] == 40.0


def test_every_listing_says_when_it_was_last_seen(db, client, card):
    """Listings end. A fetch from three weeks ago describes a shop window that
    has since changed, and the page has to be able to say so."""
    seen = datetime.now(UTC) - timedelta(days=21)
    add_listing(db, card["catalog_key"], price=40_00, seen_at=seen)

    (row,) = client.get(f"/api/cards/{card['id']}/market/listings").json()

    assert row["seen_at"] is not None
    assert row["seen_at"].startswith(seen.date().isoformat())


def test_listings_that_stopped_appearing_are_not_shown(db, client, card):
    """A sync marks vanished listings inactive rather than deleting them, so the
    endpoint has to filter rather than assume the table is current."""
    add_listing(db, card["catalog_key"], price=40_00)
    add_listing(db, card["catalog_key"], price=39_00, is_active=False)

    rows = client.get(f"/api/cards/{card['id']}/market/listings").json()

    assert [row["price"] for row in rows] == [40.0]


def test_an_auction_carries_its_closing_time(db, client, card):
    ends = datetime.now(UTC) + timedelta(days=2)
    add_listing(db, card["catalog_key"], price=40_00, is_auction=True, ends_at=ends)

    (row,) = client.get(f"/api/cards/{card['id']}/market/listings").json()

    assert row["is_auction"] is True
    assert row["ends_at"] is not None


def test_a_card_with_no_identity_has_nothing_to_look_up(client):
    """`catalog_key` is how a listing finds a card at all."""
    bare = client.post("/api/cards", json={"name": "Unknown Pikachu"}).json()

    assert client.get(f"/api/cards/{bare['id']}/market/listings").json() == []


def test_another_card_s_listings_are_not_borrowed(db, client, card):
    other = client.post(
        "/api/cards", json={"name": "Charizard", "set_code": "BS", "card_number": "4/102"}
    ).json()
    add_listing(db, other["catalog_key"], price=500_00)

    assert client.get(f"/api/cards/{card['id']}/market/listings").json() == []
