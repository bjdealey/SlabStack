"""Packing the collection into submissions that still pay once packed.

The test that matters most here is the re-verification one: an optimiser that
groups cards by "what the engine recommended at twenty-five" and then proposes a
batch of six has quietly changed the economics of every card in it.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

TODAY = date.today()

BLANK_FACE = dict.fromkeys(
    (
        "corner_tl", "corner_tr", "corner_bl", "corner_br", "edge_condition",
        "surface_condition", "holo_condition", "scratches", "print_lines", "silvering",
        "whitening", "dents", "dimpling", "creases", "staining", "misc_defects",
    ),
    "none",
)


def company_id(client: TestClient, code: str) -> str:
    return next(c["id"] for c in client.get("/api/grading/companies").json() if c["code"] == code)


def add_sale(client: TestClient, card_id: str, days: int, price: float, **kwargs) -> None:
    payload = {"sale_date": (TODAY - timedelta(days=days)).isoformat(), "sale_price": price}
    payload.update(kwargs)
    response = client.post(f"/api/cards/{card_id}/market/sales", json=payload)
    assert response.status_code == 201, response.text


def assess(client: TestClient, card_id: str) -> None:
    centering = {"left": 51, "right": 49, "top": 50, "bottom": 50}
    client.put(
        f"/api/cards/{card_id}/condition",
        json={
            "centering": {"front": centering, "back": centering},
            "front": BLANK_FACE,
            "back": BLANK_FACE,
        },
    )


def make_card(client: TestClient, name: str, number: str, **kwargs) -> dict:
    return client.post(
        "/api/cards", json={"name": name, "set_code": "EVS", "card_number": number, **kwargs}
    ).json()


def seed_profitable(client: TestClient, card_id: str, *, top: float = 900.0) -> None:
    """Raw plus a full CGC ladder, priced so grading clearly pays."""
    grader = company_id(client, "CGC")
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200)
    ladder = ((10.0, top), (9.5, top * 0.68), (9.0, top * 0.45),
              (8.5, top * 0.36), (8.0, top * 0.29), (7.5, top * 0.26))
    for grade, price in ladder:
        for index in range(6):
            add_sale(client, card_id, days=index * 9, price=price, company_id=grader, grade=grade)


def seed_marginal(client: TestClient, card_id: str) -> None:
    """Slabs worth a little more than raw — enough at a big batch, not at a small one."""
    grader = company_id(client, "CGC")
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200)
    for grade, price in ((10.0, 292), (9.5, 276), (9.0, 263),
                         (8.5, 256), (8.0, 250), (7.5, 246)):
        for index in range(6):
            add_sale(client, card_id, days=index * 9, price=price, company_id=grader, grade=grade)


def optimise(client: TestClient, **params) -> dict:
    response = client.post("/api/submissions/optimise", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- The core promise --------------------------------------------------------


def test_a_card_that_stops_paying_in_a_small_batch_is_reported_not_shipped(client: TestClient):
    """One marginal card routed at 25 lands in a batch of 1, and must say so.

    This is the failure the optimiser exists to prevent: a plan that looks
    profitable because every card was costed against a batch that never formed.
    """
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_marginal(client, card["id"])
    assess(client, card["id"])

    body = optimise(client)
    stopped = body["stopped_paying"]
    assert len(stopped) == 1, "the marginal card cannot pay its own postage"
    assert stopped[0]["still_pays"] is False
    assert "carries a bigger share of the postage" in stopped[0]["reason"]
    assert body["expected_profit"] is None, "a card that does not pay contributes nothing"


def test_a_card_that_still_pays_alone_is_kept(client: TestClient):
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_profitable(client, card["id"])
    assess(client, card["id"])

    body = optimise(client)
    assert not body["stopped_paying"]
    assert body["placed"] == 1
    assert body["expected_profit"] > 0


def test_totals_count_only_the_cards_that_still_pay(client: TestClient):
    strong = make_card(client, "Umbreon VMAX", "215/203")
    seed_profitable(client, strong["id"])
    assess(client, strong["id"])

    marginal = make_card(client, "Sylveon VMAX", "211/203")
    seed_marginal(client, marginal["id"])
    assess(client, marginal["id"])

    body = optimise(client)
    paying = [
        card
        for batch in body["batches"]
        for card in batch["cards"]
        if card["still_pays"]
    ]
    assert round(sum(card["expected_profit"] for card in paying), 2) == body["expected_profit"]


# --- Packing -----------------------------------------------------------------


def test_cards_are_grouped_into_batches_by_grader_and_tier(client: TestClient):
    for index in range(3):
        card = make_card(client, f"Card {index}", f"{210 + index}/203")
        seed_profitable(client, card["id"])
        assess(client, card["id"])

    body = optimise(client)
    assert body["batches"], "three profitable cards should propose at least one batch"
    for batch in body["batches"]:
        assert batch["company_code"]
        assert batch["card_count"] == len(batch["cards"])


def test_a_batch_short_of_its_tier_minimum_says_how_many_more_it_needs(client: TestClient):
    """Costed as it stands, so filling it can be valued rather than guessed at."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_profitable(client, card["id"])
    assess(client, card["id"])

    body = optimise(client)
    short = [batch for batch in body["batches"] if not batch["viable"]]
    for batch in short:
        assert batch["short_by"] > 0
        assert str(batch["short_by"]) in batch["reason"]
        assert "needs" in batch["reason"]


def test_a_short_batch_prices_the_tier_the_cards_actually_land_on(client: TestClient):
    """Nine cards routed to Bulk are Economy cards: Bulk needs twenty-five.

    Labelling them Bulk while quoting Economy's price would describe a route
    that does not exist at that size — the same mistake as pairing one grader's
    fee with another's slab.
    """
    for index in range(9):
        card = make_card(client, f"Marginal {index}", f"{180 + index}/203")
        seed_marginal(client, card["id"])
        assess(client, card["id"])

    body = optimise(client)
    short = next(batch for batch in body["batches"] if not batch["viable"])
    assert short["tier_name"] == "Bulk", "routed there when a full batch was assumed"
    assert short["effective_tier_name"] != "Bulk", "but Bulk needs 25 and this has 9"
    assert f"graded at {short['effective_tier_name']}" in short["reason"]

    for card in short["cards"]:
        assert card["tier_name"] == short["effective_tier_name"]

    # And the cost quoted per card is that tier's price at this size, not Bulk's.
    first = short["cards"][0]
    evaluation = client.get(
        f"/api/cards/{first['card_id']}/evaluation",
        params={"batch_size": short["card_count"]},
    ).json()
    matching = next(
        row
        for row in evaluation["expected_outcomes"]["outcomes"]
        if row["tier_name"] == first["tier_name"]
    )
    assert first["grading_cost"] == matching["grading_cost"]


def test_filling_a_short_batch_is_valued_rather_than_just_requested(client: TestClient):
    """"Add sixteen more cards" is an instruction; with a number it is a decision."""
    for index in range(9):
        card = make_card(client, f"Marginal {index}", f"{180 + index}/203")
        seed_marginal(client, card["id"])
        assess(client, card["id"])

    body = optimise(client)
    short = next(batch for batch in body["batches"] if not batch["viable"])
    assert short["expected_profit_if_filled"] > short["expected_profit"]
    assert "is worth" in short["reason"]


def test_a_viable_batch_lands_on_the_tier_it_was_routed_to(client: TestClient):
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_profitable(client, card["id"])
    assess(client, card["id"])

    body = optimise(client)
    for batch in body["batches"]:
        if batch["viable"]:
            assert batch["effective_tier_name"] == batch["tier_name"]
            assert batch["expected_profit_if_filled"] is None, "nothing to fill"


def test_the_routing_batch_size_is_reported(client: TestClient):
    """Routing at 1 would hide the bulk tiers, so the size used has to be visible."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_profitable(client, card["id"])
    assess(client, card["id"])

    body = optimise(client)
    assert body["routed_at_batch_size"] > 1


def test_a_cheaper_tier_is_suggested_with_what_it_saves(client: TestClient):
    for index in range(3):
        card = make_card(client, f"Card {index}", f"{210 + index}/203")
        seed_profitable(client, card["id"], top=400.0)
        assess(client, card["id"])

    body = optimise(client)
    suggestions = [
        card
        for batch in body["batches"]
        for card in batch["cards"]
        if card["cheaper_tier_name"]
    ]
    for card in suggestions:
        assert card["cheaper_tier_saving"] > 0
        assert card["cheaper_tier_name"] != card["tier_name"]


# --- Honesty about what it could not do --------------------------------------


def test_an_empty_collection_says_there_is_nothing_to_plan(client: TestClient):
    body = optimise(client)
    assert body["status"] == "insufficient_data"
    assert body["expected_profit"] is None
    assert "nothing can be decided" in body["reason"] or "no submission to build" in body["reason"]


def test_a_collection_with_nothing_worth_grading_says_that_instead(client: TestClient):
    card = make_card(client, "Bidoof", "111/203", user_raw_value=3.0)
    for index in range(8):
        add_sale(client, card["id"], days=index * 5, price=3.0)
    assess(client, card["id"])

    body = optimise(client)
    assert body["status"] == "insufficient_data"
    assert "clears the bar" in body["reason"]
    assert body["analysable"] == 1, "it was analysable — it just was not worth grading"


def test_the_limit_is_reported_rather_than_silently_cutting(client: TestClient):
    for index in range(3):
        card = make_card(client, f"Card {index}", f"{210 + index}/203")
        seed_profitable(client, card["id"])
        assess(client, card["id"])

    body = optimise(client, limit=2)
    assert body["truncated"] is True
    assert any("2 most recently updated" in note for note in body["notes"])
    assert body["analysable"] == 3, "it counted them all, it just did not evaluate them all"
