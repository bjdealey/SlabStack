"""What a card actually fetched, and whether the engine was right about it.

Every other figure in this build is a projection. These tests are about the one
that is not, and about the two ways a realised profit can be quietly wrong:
counting money that was never received, and dropping a cost that was.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Card, CardDisposal, PriceSnapshot
from app.services import cards_service, disposals

TODAY = date.today()


@pytest.fixture
def db(seeded_db):
    """Selling profiles present, so costs can be estimated from one."""
    return seeded_db


@pytest.fixture
def card(db) -> Card:
    row = Card(
        name="Umbreon VMAX",
        set_code="EVS",
        card_number="215/203",
        language="English",
        purchase_price_minor=120_00,
    )
    # Through the real path, so `catalog_key` is derived the way a saved card
    # gets it — a snapshot keyed off None would test nothing.
    cards_service.resolve_references(db, row)
    db.add(row)
    db.commit()
    return row


def snapshot(db, key: str, label: str, value_minor: int, on: date) -> None:
    db.add(
        PriceSnapshot(
            catalog_key=key,
            grade_label=label,
            snapshot_date=on,
            value_minor=value_minor,
            currency="GBP",
        )
    )
    db.commit()


# --- Recording ----------------------------------------------------------------


def test_a_price_and_a_date_is_the_whole_of_the_common_case(db, card):
    """Costs come from the selling profile so recording a sale is not a form."""
    row = disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=300_00)
    db.commit()

    assert row.gross_minor == 300_00
    assert row.fees_minor and row.fees_minor > 0, "estimated, not left blank"
    assert row.net_proceeds_minor < 300_00
    assert row.net_is_user_entered is False


def test_a_payout_you_type_wins_over_every_estimate(db, card):
    """A payout statement is a fact; a fee model is not."""
    row = disposals.record_disposal(
        db, card, sold_on=TODAY, gross_minor=300_00, net_proceeds_minor=271_43
    )
    db.commit()

    assert row.net_proceeds_minor == 271_43
    assert row.net_is_user_entered is True


def test_recording_a_sale_marks_the_card_sold(db, card):
    disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=300_00)
    db.commit()

    assert db.get(Card, card.id).status == "sold"


def test_the_identity_survives_the_card_being_deleted(db, card):
    """Deleting a card should lose the card, not the lesson."""
    disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=300_00)
    db.commit()
    db.delete(card)
    db.commit()

    row = db.scalar(select(CardDisposal))
    assert row is not None
    assert row.card_id is None
    assert row.card_name == "Umbreon VMAX"


# --- The refusal that matters most --------------------------------------------


def test_a_profit_missing_a_cost_is_not_reported_as_a_profit(db):
    """It would be wrong in the flattering direction, which is the bias this
    whole application exists to correct."""
    bare = Card(name="No Purchase Price", set_code="EVS", card_number="1/203")
    cards_service.resolve_references(db, bare)
    db.add(bare)
    db.commit()
    disposals.record_disposal(db, bare, sold_on=TODAY, gross_minor=300_00)
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.net_proceeds is not None, "the proceeds are real"
    assert outcome.realised_profit is None, "the profit is not"
    assert outcome.profit_is_complete is False
    assert "what you paid for it" in (outcome.reason or "")


def test_a_graded_sale_without_a_grading_cost_is_incomplete_too(db, card):
    """Null grading cost means unrecorded, never free."""
    disposals.record_disposal(
        db, card, sold_on=TODAY, gross_minor=900_00, sold_graded=True, grade_label="PSA 10"
    )
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.realised_profit is None
    assert "what grading cost" in (outcome.reason or "")


def test_incomplete_sales_are_counted_in_proceeds_and_left_out_of_profit(db, card):
    bare = Card(name="No Price", set_code="EVS", card_number="2/203")
    cards_service.resolve_references(db, bare)
    db.add(bare)
    db.commit()
    disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=300_00, net_proceeds_minor=270_00)
    disposals.record_disposal(db, bare, sold_on=TODAY, gross_minor=100_00, net_proceeds_minor=90_00)
    db.commit()

    report = disposals.realised(db)

    assert report.sold == 2
    assert report.scored == 1
    assert report.total_net_proceeds == 360.0, "both sales"
    assert report.total_realised_profit == 150.0, "only the one that can be scored"
    assert any("left out of the profit" in note for note in report.notes)


# --- Scoring ------------------------------------------------------------------


def test_realised_profit_is_proceeds_less_what_you_paid(db, card):
    disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=300_00, net_proceeds_minor=270_00)
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.purchase_price == 120.0
    assert outcome.realised_profit == 150.0
    assert outcome.profit_is_complete is True


def test_grading_cost_comes_out_of_the_profit(db, card):
    disposals.record_disposal(
        db,
        card,
        sold_on=TODAY,
        gross_minor=900_00,
        sold_graded=True,
        grade_label="PSA 10",
        net_proceeds_minor=800_00,
        grading_cost_minor=60_00,
    )
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.realised_profit == 620.0, "800 − 120 paid − 60 grading"


def test_a_sale_is_scored_against_the_market_on_the_day_not_today(db, card):
    """Today's price has moved for reasons that have nothing to do with the
    decision being scored."""
    sold_on = TODAY - timedelta(days=30)
    snapshot(db, card.catalog_key, "raw", 300_00, sold_on)
    snapshot(db, card.catalog_key, "raw", 900_00, TODAY)  # the market ran away after
    disposals.record_disposal(
        db, card, sold_on=sold_on, gross_minor=300_00, net_proceeds_minor=270_00
    )
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.market_value_on_the_day == 300.0, "not 900"


def test_the_market_comparison_is_gross_against_gross(db, card):
    """A snapshot is a sale price, so measuring the *payout* against it reports
    the fee load as though it were selling badly — every sale would read about a
    tenth under the market and the number would say nothing about the sale."""
    snapshot(db, card.catalog_key, "raw", 300_00, TODAY)
    disposals.record_disposal(
        db, card, sold_on=TODAY, gross_minor=300_00, net_proceeds_minor=262_00
    )
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.vs_market_pct == 0.0, "sold exactly at market, whatever the fees took"


def test_selling_under_the_market_is_reported_as_such(db, card):
    snapshot(db, card.catalog_key, "raw", 300_00, TODAY)
    disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=270_00)
    db.commit()

    assert disposals.realised(db).items[0].vs_market_pct == -10.0


def test_a_slab_is_compared_against_what_the_raw_card_was_worth_that_day(db, card):
    """"Was grading worth it" answered with two numbers that both happened."""
    snapshot(db, card.catalog_key, "raw", 300_00, TODAY)
    disposals.record_disposal(
        db,
        card,
        sold_on=TODAY,
        gross_minor=900_00,
        sold_graded=True,
        grade_label="PSA 10",
        net_proceeds_minor=800_00,
        grading_cost_minor=60_00,
    )
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.raw_value_on_the_day == 300.0
    assert outcome.grading_gain == 440.0, "800 netted − 300 raw − 60 grading"


def test_no_price_history_means_no_comparison_rather_than_a_guess(db, card):
    disposals.record_disposal(db, card, sold_on=TODAY, gross_minor=300_00)
    db.commit()

    outcome = disposals.realised(db).items[0]

    assert outcome.market_value_on_the_day is None
    assert outcome.vs_market_pct is None


def test_nothing_sold_says_so_rather_than_reporting_zero(db):
    report = disposals.realised(db)

    assert report.status == "insufficient_data"
    assert report.total_realised_profit is None, "not 0.0 — nothing has happened"


# --- Through the API ----------------------------------------------------------


def test_the_endpoint_records_and_reads_back(client, db, card):
    body = client.post(
        f"/api/cards/{card.id}/sold",
        json={"sold_on": TODAY.isoformat(), "gross": 300.0, "net_proceeds": 270.0},
    ).json()

    assert body["net_proceeds"] == 270.0
    assert body["net_is_user_entered"] is True
    assert client.get(f"/api/cards/{card.id}/sold").json()["id"] == body["id"]


def test_a_card_cannot_be_sold_twice(client, db, card):
    """Two records would double the realised profit."""
    payload = {"sold_on": TODAY.isoformat(), "gross": 300.0}
    assert client.post(f"/api/cards/{card.id}/sold", json=payload).status_code == 201

    second = client.post(f"/api/cards/{card.id}/sold", json=payload)

    assert second.status_code == 409
    assert "already recorded as sold" in second.json()["error"]["message"]


def test_undoing_a_sale_returns_the_card_to_the_collection(client, db, card):
    client.post(f"/api/cards/{card.id}/sold", json={"sold_on": TODAY.isoformat(), "gross": 300.0})

    assert client.delete(f"/api/cards/{card.id}/sold").status_code == 204
    assert client.get(f"/api/cards/{card.id}").json()["status"] == "in_collection"
    assert db.scalars(select(CardDisposal)).all() == []


def test_a_card_never_sold_reads_as_null_not_as_an_error(client, card):
    assert client.get(f"/api/cards/{card.id}/sold").json() is None


def test_the_realised_endpoint_totals_what_happened(client, db, card):
    client.post(
        f"/api/cards/{card.id}/sold",
        json={"sold_on": TODAY.isoformat(), "gross": 300.0, "net_proceeds": 270.0},
    )

    body = client.get("/api/analytics/realised").json()

    assert body["sold"] == 1
    assert body["scored"] == 1
    assert body["total_realised_profit"] == 150.0
    assert body["raw_sales"] == 1
