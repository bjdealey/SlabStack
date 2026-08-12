"""Which unassessed cards are worth five minutes, and which are settled already.

Importing four hundred cards takes a second; assessing four hundred does not.
The decision engine cannot help — it needs an assessment before it says anything
— so this ranks on a **ceiling**: the best-netting grade with sales behind it,
less what the card already nets raw, less what grading costs.

That makes it an upper bound rather than a forecast, and the tests here are
mostly about the two ways a bound can be misread: treating it as a prediction,
and treating "the best grade we have a price for" as "the best grade".
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Card, ConditionAssessment, MarketSale
from app.services import analytics, cards_service, market_service, settings_service
from app.services.market_service import MarketParameters

TODAY = date.today()


@pytest.fixture
def db(seeded_db):
    """Reference data present: grading companies, tiers and selling profiles.

    Without them there is no tier to cost and no ladder to compare a grade
    against, so every ceiling would be `None` and the tests would pass by
    accident on the "cannot tell" branch.
    """
    return seeded_db


@pytest.fixture
def build(db):
    """A card with raw sales, and optionally sales at one graded label."""

    def _build(
        name: str,
        number: str,
        raw_minor: int,
        graded_minor: int | None = None,
        label: str = "CGC 10",
        grade: float | None = 10.0,
        count: int = 10,
    ) -> Card:
        card = Card(
            name=name,
            set_name="Evolving Skies",
            set_code="EVS",
            card_number=number,
            language="English",
        )
        cards_service.resolve_references(db, card)
        db.add(card)
        db.flush()
        for index in range(count):
            db.add(
                MarketSale(
                    catalog_key=card.catalog_key,
                    card_id=card.id,
                    grade_label="raw",
                    sale_date=TODAY - timedelta(days=index * 5 + 2),
                    sale_price_minor=raw_minor,
                    currency="GBP",
                    listing_title=f"{name} raw",
                )
            )
            if graded_minor:
                db.add(
                    MarketSale(
                        catalog_key=card.catalog_key,
                        card_id=card.id,
                        grade_label=label,
                        grade=grade,
                        sale_date=TODAY - timedelta(days=index * 6 + 3),
                        sale_price_minor=graded_minor,
                        currency="GBP",
                        listing_title=f"{label} {name}",
                    )
                )
        db.commit()
        values = settings_service.get_all(db)
        market_service.recompute_key(
            db,
            card.catalog_key,
            params=MarketParameters.from_settings(values),
            currency=values.get("currency", "GBP"),
        )
        db.commit()
        return card

    return _build


def only(queue, name: str):
    return next(item for item in queue.items if item.name.startswith(name))


# --- What it puts in front of you ---------------------------------------------


def test_a_big_graded_premium_is_worth_assessing(db, build):
    build("Umbreon VMAX", "215/203", 300_00, 900_00)

    item = only(analytics.assessment_queue(db), "Umbreon")

    assert item.verdict == "assess"
    assert item.ceiling and item.ceiling > 400
    assert item.best_grade_label == "CGC 10"


def test_the_queue_is_ranked_by_ceiling(db, build):
    build("Small", "1/203", 40_00, 110_00)
    build("Huge", "2/203", 300_00, 900_00)

    queue = analytics.assessment_queue(db)

    assert [item.name.split()[0] for item in queue.items][:2] == ["Huge", "Small"]


# --- What it settles without you looking --------------------------------------


def test_a_card_that_loses_money_at_its_best_grade_is_ruled_out(db, build):
    """The point of a ceiling: no condition can beat it, so nothing needs
    assessing."""
    build("Bidoof", "111/172", 3_00, 12_00)

    item = only(analytics.assessment_queue(db), "Bidoof")

    assert item.verdict == "skip"
    assert item.ceiling and item.ceiling < 0
    assert "No condition changes that" in (item.reason or "")


def test_a_ceiling_under_your_own_bar_is_also_settled(db, build):
    """A best case of £2 is not worth five minutes, and the bar for that is the
    user's own `minimum_absolute_profit`, not a second opinion invented here."""
    build("Mid Card", "100/203", 40_00, 110_00)

    item = only(analytics.assessment_queue(db), "Mid Card")

    assert item.verdict == "skip"
    assert item.ceiling and 0 < item.ceiling < 25
    assert "under the" in (item.reason or "")


def test_the_bar_moves_with_the_setting(db, build):
    """Configuration, not a constant: lower the bar and the same card qualifies."""
    build("Mid Card", "100/203", 40_00, 110_00)
    assert only(analytics.assessment_queue(db), "Mid Card").verdict == "skip"

    settings_service.set_many(db, {"minimum_absolute_profit": 1.0})
    db.commit()

    assert only(analytics.assessment_queue(db), "Mid Card").verdict == "assess"


# --- The refusal that matters most --------------------------------------------


def test_a_bound_over_a_partial_ladder_never_rules_a_card_out(db, build):
    """If the best grade with sales behind it is a 9, a 10 might pay well.

    Calling that a skip would throw away a card on the strength of a price that
    was never looked up — the ceiling is only a ceiling over what is priced.
    """
    build("Nine Only", "101/203", 40_00, 55_00, label="CGC 9", grade=9.0)

    item = only(analytics.assessment_queue(db), "Nine Only")

    assert item.ceiling and item.ceiling < 0, "it does not pay on what is priced"
    assert item.verdict == "unknown", "and yet it is not ruled out"
    assert item.ceiling_is_complete is False
    assert "missing prices" in (item.reason or "")


def test_a_card_with_no_graded_sales_says_which_data_is_missing(db, build):
    build("Raw Only", "102/203", 80_00, None)

    item = only(analytics.assessment_queue(db), "Raw Only")

    assert item.verdict == "unknown"
    assert item.ceiling is None
    assert "sales are stored" in (item.reason or "")


def test_a_grader_with_no_priced_tier_is_named_as_configuration(db, build):
    """PSA ships with no fees entered, so it silently withholds a verdict on
    every card it could otherwise have priced. Saying so is one click from a
    fix; a joined list of every company's complaint is not."""
    build("Raw Only", "102/203", 80_00, None)

    item = only(analytics.assessment_queue(db), "Raw Only")

    assert "no priced tier configured" in (item.reason or "")
    assert "Settings" in (item.reason or "")


# --- Who is in the queue at all -----------------------------------------------


def test_assessed_cards_are_not_in_the_queue(db, client, build):
    """They are the decision engine's business, and being in both lists would
    ask the user to do work that is already done."""
    card = build("Umbreon VMAX", "215/203", 300_00, 900_00)
    db.add(ConditionAssessment(card_id=card.id, is_current=True))
    db.commit()

    assert analytics.assessment_queue(db).items == []


def test_a_card_with_no_price_is_not_ranked_and_not_counted_against_you(db):
    """Nothing can be said about it yet, and it is waiting on market data rather
    than on you."""
    from app.models import Card as CardModel

    db.add(CardModel(name="Unpriced", set_code="EVS", card_number="9/203"))
    db.commit()

    queue = analytics.assessment_queue(db)

    assert queue.analysed == 0
    assert queue.status == "insufficient_data"


def test_a_capped_run_says_it_was_capped(db, build):
    for index in range(3):
        build(f"Card {index}", f"{index}/203", 300_00, 900_00)

    queue = analytics.assessment_queue(db, limit=1)

    assert len(queue.items) == 1
    assert queue.truncated is True
    assert any("first 1" in note for note in queue.notes)


def test_the_ceiling_is_never_presented_as_a_forecast(db, build):
    build("Umbreon VMAX", "215/203", 300_00, 900_00)

    queue = analytics.assessment_queue(db)

    assert any("not forecasts" in note for note in queue.notes)


# --- Through the API ----------------------------------------------------------


def test_the_endpoint_returns_the_queue(client, db, build):
    build("Umbreon VMAX", "215/203", 300_00, 900_00)
    build("Bidoof", "111/172", 3_00, 12_00)

    body = client.get("/api/analytics/assessment-queue").json()

    assert body["worth_assessing"] == 1
    assert body["ruled_out"] == 1
    assert body["items"][0]["verdict"] == "assess"
    assert body["items"][0]["ceiling"] > 400


def test_the_endpoint_carries_the_batch_size_through(client, db, build):
    """Shipping belongs to the parcel, so a ceiling costed at one card is the
    honest worst case and a fuller batch raises it."""
    build("Umbreon VMAX", "215/203", 300_00, 900_00)

    alone = client.get("/api/analytics/assessment-queue?batch_size=1").json()
    batched = client.get("/api/analytics/assessment-queue?batch_size=25").json()

    assert batched["items"][0]["ceiling"] > alone["items"][0]["ceiling"]


def test_cards_are_counted_even_when_none_can_be_ranked(client, db, build):
    build("Raw Only", "102/203", 80_00, None)

    body = client.get("/api/analytics/assessment-queue").json()

    assert body["analysed"] == 1
    assert body["unknown"] == 1
    assert body["status"] == "insufficient_data"
    assert "no graded sales" in (body["reason"] or "")


def test_nothing_is_written_by_looking(client, db, build):
    """A ranked view must not create assessments as a side effect."""
    build("Umbreon VMAX", "215/203", 300_00, 900_00)
    client.get("/api/analytics/assessment-queue")

    assert db.scalars(select(ConditionAssessment)).all() == []


def test_the_missing_data_reason_reads_as_a_sentence(db, build):
    """It is read by a person, not parsed: "ACE, CGC" is a list, "ACE or CGC"
    is English."""
    build("Raw Only", "102/203", 80_00, None)

    reason = only(analytics.assessment_queue(db), "Raw Only").reason or ""

    assert "ACE or CGC" in reason
    assert ", CGC sales" not in reason
