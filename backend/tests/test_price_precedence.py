"""Which price wins when your sales and a provider's index both exist.

`market_prices` is unique per `(catalog_key, grade_label, source_id)` so the two
can coexist, which is the right storage shape and exactly what makes this
question necessary: asked for "the raw price", something has to choose.

The rule is that your own sales win. These tests exist because getting it wrong
fails silently — the engine would keep producing a confident number, just
occasionally the wrong one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import DataSource, MarketPrice
from app.services import market_service

TODAY = date.today()


@pytest.fixture
def card(client: TestClient) -> dict:
    return client.post(
        "/api/cards", json={"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"}
    ).json()


def add_sale(client: TestClient, card_id: str, days: int, price: float) -> None:
    client.post(
        f"/api/cards/{card_id}/market/sales",
        json={"sale_date": (TODAY - timedelta(days=days)).isoformat(), "sale_price": price},
    )


def provider_price(
    db,
    catalog_key: str,
    *,
    median: int,
    label: str = "raw",
    source_code: str = "pokemontcg_io",
    computed_at: datetime | None = None,
) -> MarketPrice:
    """A price row as a provider would write it: no sample, because it has none."""
    source = db.scalar(select(DataSource).where(DataSource.code == source_code))
    row = MarketPrice(
        catalog_key=catalog_key,
        grade_label=label,
        median_minor=median,
        realistic_sale_minor=median,
        sample_size=0,
        source_id=source.id,
        computed_at=computed_at or datetime.now(),
    )
    db.add(row)
    db.commit()
    return row


def test_your_own_sales_beat_a_providers_index(client: TestClient, db, card):
    """Twenty-two real sales of this card outrank a third party's aggregate."""
    for index in range(22):
        add_sale(client, card["id"], days=index * 4, price=200)
    db.expire_all()

    stored = db.scalar(select(MarketPrice).where(MarketPrice.catalog_key.is_not(None)))
    provider_price(db, stored.catalog_key, median=9_900)

    resolved = market_service.prices_for(db, stored.catalog_key)
    raw = next(row for row in resolved if row.grade_label == "raw")
    assert raw.source_id is None, "the sale-derived row wins"
    assert raw.sample_size == 22


def test_one_row_per_grade_however_many_sources(client: TestClient, db, card):
    """The engines ask for "the raw price" and must get exactly one."""
    add_sale(client, card["id"], days=1, price=200)
    db.expire_all()
    stored = db.scalar(select(MarketPrice))
    provider_price(db, stored.catalog_key, median=9_900)
    provider_price(db, stored.catalog_key, median=8_800, source_code="pricecharting")

    resolved = market_service.prices_for(db, stored.catalog_key)
    labels = [row.grade_label for row in resolved]
    assert labels == sorted(set(labels)), "no duplicates"
    assert len(resolved) == 1


def test_a_provider_price_is_used_when_you_have_no_sales(client: TestClient, db, card):
    """The common case on a fresh collection — better than nothing at all."""
    stored_key = client.get(f"/api/cards/{card['id']}").json()["catalog_key"]
    provider_price(db, stored_key, median=9_900)

    resolved = market_service.prices_for(db, stored_key)
    assert len(resolved) == 1
    assert resolved[0].source_id is not None
    assert resolved[0].median_minor == 9_900


def test_an_empty_sale_derived_row_does_not_beat_a_real_provider_price(
    client: TestClient, db, card
):
    """A row left over from sales that were all excluded describes nothing."""
    stored_key = client.get(f"/api/cards/{card['id']}").json()["catalog_key"]
    db.add(
        MarketPrice(
            catalog_key=stored_key,
            grade_label="raw",
            median_minor=None,
            sample_size=0,
            source_id=None,
        )
    )
    db.commit()
    provider_price(db, stored_key, median=9_900)

    resolved = market_service.prices_for(db, stored_key)
    assert resolved[0].source_id is not None, "an empty own-row is not evidence"
    assert resolved[0].median_minor == 9_900


def test_between_two_provider_rows_the_fresher_wins(client: TestClient, db, card):
    """Neither has a sample, so recency is the only thing left to go on."""
    stored_key = client.get(f"/api/cards/{card['id']}").json()["catalog_key"]
    provider_price(
        db, stored_key, median=5_000, computed_at=datetime.now() - timedelta(days=30)
    )
    provider_price(db, stored_key, median=7_000, source_code="pricecharting")

    resolved = market_service.prices_for(db, stored_key)
    assert resolved[0].median_minor == 7_000


def test_comparing_rows_never_raises_on_a_missing_timestamp(client: TestClient, db, card):
    """SQLite reads timestamps back naive; a null one must not blow up either."""
    stored_key = client.get(f"/api/cards/{card['id']}").json()["catalog_key"]
    source = db.scalar(select(DataSource).where(DataSource.code == "pokemontcg_io"))
    other = db.scalar(select(DataSource).where(DataSource.code == "pricecharting"))
    db.add(MarketPrice(catalog_key=stored_key, grade_label="raw", sample_size=0,
                       source_id=source.id, computed_at=None))
    db.add(MarketPrice(catalog_key=stored_key, grade_label="raw", sample_size=0,
                       source_id=other.id, computed_at=None))
    db.commit()

    resolved = market_service.prices_for(db, stored_key)
    assert len(resolved) == 1


def test_the_card_page_says_where_a_price_came_from(client: TestClient, db, card):
    """A number standing in for your sales has to admit that it is."""
    stored_key = client.get(f"/api/cards/{card['id']}").json()["catalog_key"]
    provider_price(db, stored_key, median=9_900)

    market = client.get(f"/api/cards/{card['id']}/evaluation").json()["market"]
    assert market["raw"]["source_code"] == "pokemontcg_io"
    assert market["raw"]["source_name"]


def test_your_own_price_reports_no_source(client: TestClient, db, card):
    """Null means "your sales", which needs no attribution."""
    for index in range(10):
        add_sale(client, card["id"], days=index * 5, price=150)

    market = client.get(f"/api/cards/{card['id']}/evaluation").json()["market"]
    assert market["raw"]["source_code"] is None
    assert market["raw"]["sample_size"] == 10
