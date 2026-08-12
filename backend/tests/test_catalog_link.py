"""Linking a whole collection at once, and everything it refuses to link.

This is the code in the build most capable of doing quiet, lasting damage. A
wrong link is not a wrong answer on screen — it is every future refresh pricing
a different printing, at plausible figures, for as long as the card is owned.

So most of what is tested here is the declining: a name with nothing to pin it
down, a runner-up too close to call, a confidence too low to act on. The one
test that matters more than the rest is that a dry run writes nothing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Card, DataSource
from app.services import catalog_link
from app.services.market_data.base import CardMatch, ProviderCapabilities


class FakeProvider:
    """Returns whatever a test hands it, so the rules are what is under test."""

    requires_external_id = True

    def __init__(self, matches: list[CardMatch] | Exception):
        self.matches = matches
        self.queries: list[object] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(search=True, current_price=True)

    def search_card(self, query):
        self.queries.append(query)
        if isinstance(self.matches, Exception):
            raise self.matches
        return self.matches


def match(external_id: str, name: str, confidence: float, number: str | None = None) -> CardMatch:
    return CardMatch(
        external_id=external_id, name=name, card_number=number, confidence=confidence
    )


@pytest.fixture
def source(db) -> DataSource:
    row = db.scalar(select(DataSource).where(DataSource.code == "pokemontcg_io"))
    row.enabled = True
    db.commit()
    return row


def add_card(client: TestClient, **fields) -> dict:
    payload = {"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"}
    payload.update(fields)
    return client.post("/api/cards", json=payload).json()


def run(db, source, monkeypatch, matches, **kwargs) -> catalog_link.LinkReport:
    provider = FakeProvider(matches)
    monkeypatch.setattr(catalog_link, "load_provider", lambda _source: provider)
    return catalog_link.link_collection(db, source, **kwargs)


# --- What it links ------------------------------------------------------------


def test_one_clear_winner_is_linked(db, client, source, monkeypatch):
    card = add_card(client)
    report = run(
        db,
        source,
        monkeypatch,
        [match("swsh7-215", "Umbreon VMAX", 1.0, "215/203"), match("swsh7-95", "Umbreon VMAX", 0.3)],
        dry_run=False,
    )
    db.commit()

    assert report.linked == 1
    assert db.get(Card, card["id"]).external_ids["pokemontcg_io"] == "swsh7-215"


def test_a_sole_candidate_with_a_high_score_is_enough(db, client, source, monkeypatch):
    """No runner-up means no choice to get wrong."""
    add_card(client)
    report = run(db, source, monkeypatch, [match("swsh7-215", "Umbreon VMAX", 0.9)], dry_run=False)

    assert report.linked == 1


# --- What it refuses ----------------------------------------------------------


def test_two_close_candidates_are_handed_back(db, client, source, monkeypatch):
    """A choice belongs to the user, and a wrong one is silent for years."""
    card = add_card(client)
    report = run(
        db,
        source,
        monkeypatch,
        [match("swsh7-215", "Umbreon VMAX", 0.9), match("swsh7-214", "Umbreon VMAX", 0.85)],
        dry_run=False,
    )
    db.commit()

    assert report.ambiguous == 1
    assert report.linked == 0
    assert not (db.get(Card, card["id"]).external_ids or {}).get("pokemontcg_io")
    assert len(report.cards[0].candidates) == 2, "shows what it was choosing between"


def test_a_weak_best_match_is_not_acted_on(db, client, source, monkeypatch):
    add_card(client)
    report = run(db, source, monkeypatch, [match("swsh7-215", "Umbreon VMAX", 0.4)], dry_run=False)

    assert report.ambiguous == 1
    assert "sure" in (report.cards[0].reason or "")


def test_a_card_with_only_a_name_is_never_linked_automatically(db, client, source, monkeypatch):
    """Twelve cards are called Pikachu and a provider will rank one of them first."""
    client.post("/api/cards", json={"name": "Pikachu"})
    report = run(db, source, monkeypatch, [match("base1-58", "Pikachu", 1.0)], dry_run=False)

    assert report.linked == 0
    assert report.skipped == 1
    assert "Only a name" in (report.cards[0].reason or "")


def test_no_match_at_all_is_reported_not_forced(db, client, source, monkeypatch):
    add_card(client)
    report = run(db, source, monkeypatch, [], dry_run=False)

    assert report.linked == 0
    assert report.skipped == 1


def test_one_failing_card_does_not_abort_the_run(db, client, source, monkeypatch):
    from app.services.market_data.http import ProviderRequestError

    add_card(client)
    report = run(db, source, monkeypatch, ProviderRequestError("upstream is down"), dry_run=False)

    assert report.failed == 1
    assert report.status == "error"
    assert "down" in (report.cards[0].reason or "")


# --- The property that matters most -------------------------------------------


def test_a_dry_run_writes_nothing(db, client, source, monkeypatch):
    """A bulk action nobody watches should be able to show its work first."""
    card = add_card(client)
    report = run(db, source, monkeypatch, [match("swsh7-215", "Umbreon VMAX", 1.0)], dry_run=True)
    db.commit()

    assert report.linked == 1, "it still reports what it would do"
    assert report.dry_run is True
    assert not (db.get(Card, card["id"]).external_ids or {}).get("pokemontcg_io")


def test_only_the_link_is_written_never_the_card_s_identity(db, client, source, monkeypatch):
    """Set name and number feed catalog_key, which is how sales find this card.

    Accepting them in bulk could re-key a card away from its own history, so the
    bulk pass stores the provider's id and touches nothing else.
    """
    card = add_card(client)
    before = db.get(Card, card["id"])
    original = (before.set_code, before.card_number, before.catalog_key)

    run(
        db,
        source,
        monkeypatch,
        [
            CardMatch(
                external_id="swsh7-215",
                name="Something Else Entirely",
                set_code="XXX",
                card_number="999/999",
                confidence=1.0,
            )
        ],
        dry_run=False,
    )
    db.commit()

    after = db.get(Card, card["id"])
    assert (after.set_code, after.card_number, after.catalog_key) == original
    assert after.external_ids["pokemontcg_io"] == "swsh7-215"


def test_already_linked_cards_are_left_alone(db, client, source, monkeypatch):
    card = add_card(client)
    db.get(Card, card["id"]).external_ids = {"pokemontcg_io": "already-set"}
    db.commit()

    report = run(db, source, monkeypatch, [match("swsh7-215", "Umbreon VMAX", 1.0)], dry_run=False)

    assert report.status == "insufficient_data"
    assert db.get(Card, card["id"]).external_ids["pokemontcg_io"] == "already-set"


def test_relink_reconsiders_them_when_asked(db, client, source, monkeypatch):
    card = add_card(client)
    db.get(Card, card["id"]).external_ids = {"pokemontcg_io": "stale"}
    db.commit()

    run(
        db, source, monkeypatch,
        [match("swsh7-215", "Umbreon VMAX", 1.0)],
        dry_run=False, relink=True,
    )
    db.commit()

    assert db.get(Card, card["id"]).external_ids["pokemontcg_io"] == "swsh7-215"


def test_a_capped_run_says_it_was_capped(db, client, source, monkeypatch):
    for number in ("1/100", "2/100", "3/100"):
        add_card(client, card_number=number)

    report = run(
        db, source, monkeypatch,
        [match("swsh7-215", "Umbreon VMAX", 1.0)],
        dry_run=True, limit=1,
    )

    assert len(report.cards) == 1
    assert any("3 card(s) are unlinked" in note for note in report.notes)


# --- Through the API ----------------------------------------------------------


def test_the_endpoint_defaults_to_a_dry_run(client, db, source, monkeypatch):
    card = add_card(client)
    provider = FakeProvider([match("swsh7-215", "Umbreon VMAX", 1.0)])
    monkeypatch.setattr(catalog_link, "load_provider", lambda _source: provider)

    body = client.post("/api/catalog/link-all").json()

    assert body["dry_run"] is True
    assert body["linked"] == 1
    assert not (db.get(Card, card["id"]).external_ids or {}).get("pokemontcg_io")


def test_the_endpoint_writes_when_told_to(client, db, source, monkeypatch):
    card = add_card(client)
    provider = FakeProvider([match("swsh7-215", "Umbreon VMAX", 1.0)])
    monkeypatch.setattr(catalog_link, "load_provider", lambda _source: provider)

    body = client.post("/api/catalog/link-all?dry_run=false").json()

    assert body["dry_run"] is False
    assert db.get(Card, card["id"]).external_ids["pokemontcg_io"] == "swsh7-215"


def test_a_source_that_cannot_search_is_refused(client, db, monkeypatch):
    """eBay is searched by name at sync time and has no catalogue to link to."""
    row = db.scalar(select(DataSource).where(DataSource.code == "ebay"))
    row.enabled = True
    db.commit()

    class NoSearch(FakeProvider):
        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(search=False, sales_history=True)

    monkeypatch.setattr(catalog_link, "load_provider", lambda _source: NoSearch([]))
    body = client.post("/api/catalog/link-all?source_code=ebay").json()

    assert body["status"] == "error"
    assert "does not offer card search" in body["reason"]
