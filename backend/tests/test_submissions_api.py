"""Building a parcel through the API, and what it refuses to let you do.

The lifecycle rules matter more than they look: once a submission has shipped,
its contents are a record of what you actually sent. Letting that be edited
would rewrite history and quietly break the Phase 8 comparison of predicted
grades against real ones.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

TODAY = date.today()


def company_id(client: TestClient, code: str = "CGC") -> str:
    return next(c["id"] for c in client.get("/api/grading/companies").json() if c["code"] == code)


def tier_id(client: TestClient, code: str, tier_name: str) -> str:
    company = next(c for c in client.get("/api/grading/companies").json() if c["code"] == code)
    return next(t["id"] for t in company["tiers"] if t["tier_name"] == tier_name)


def make_card(client: TestClient, name: str, number: str, value: float | None = 200.0) -> dict:
    payload = {"name": name, "set_code": "EVS", "card_number": number}
    if value is not None:
        payload["user_declared_value"] = value
        payload["user_raw_value"] = value
    return client.post("/api/cards", json=payload).json()


def create(client: TestClient, cards: list[dict], **kwargs) -> dict:
    payload = {
        "company_id": company_id(client),
        "tier_id": tier_id(client, "CGC", "Economy"),
        "card_ids": [card["id"] for card in cards],
        **kwargs,
    }
    response = client.post("/api/submissions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Building ----------------------------------------------------------------


def test_a_new_submission_gets_a_readable_reference(client: TestClient):
    body = create(client, [make_card(client, "Umbreon VMAX", "215/203")])
    assert body["reference"].startswith(f"SUB-{TODAY:%Y-%m}-")
    assert body["status"] == "draft"
    assert body["card_count"] == 1


def test_references_do_not_collide(client: TestClient):
    first = create(client, [make_card(client, "A", "1/1")])
    second = create(client, [make_card(client, "B", "2/2")])
    assert first["reference"] != second["reference"]


def test_adding_a_card_recosts_the_whole_parcel(client: TestClient):
    """Every card's share of the postage changes when a card joins."""
    cards = [make_card(client, f"Card {i}", f"{i}/10") for i in range(3)]
    body = create(client, cards[:1], shipping_out=20.0, shipping_return=20.0)
    alone = body["cards"][0]["allocated_overhead"]

    body = client.post(
        f"/api/submissions/{body['id']}/cards",
        json={"card_ids": [cards[1]["id"], cards[2]["id"]]},
    ).json()
    assert body["card_count"] == 3
    shared = body["cards"][0]["allocated_overhead"]
    assert shared < alone, "three cards share what one carried alone"
    assert round(sum(line["allocated_overhead"] for line in body["cards"]), 2) == body["shared_pot"]


def test_adding_a_card_twice_does_not_duplicate_it(client: TestClient):
    card = make_card(client, "Umbreon VMAX", "215/203")
    body = create(client, [card])
    body = client.post(
        f"/api/submissions/{body['id']}/cards", json={"card_ids": [card["id"]]}
    ).json()
    assert body["card_count"] == 1


def test_removing_a_card_recosts_the_rest(client: TestClient):
    cards = [make_card(client, f"Card {i}", f"{i}/10") for i in range(3)]
    body = create(client, cards, shipping_out=30.0)
    before = body["cards"][0]["allocated_overhead"]
    line_id = body["cards"][-1]["submission_card_id"]

    body = client.delete(f"/api/submissions/{body['id']}/cards/{line_id}").json()
    assert body["card_count"] == 2
    assert body["cards"][0]["allocated_overhead"] > before


def test_switching_to_value_weighted_moves_the_cost_onto_the_expensive_card(
    client: TestClient,
):
    cheap = make_card(client, "Common", "1/10", value=4.0)
    dear = make_card(client, "Alt Art", "2/10", value=900.0)
    body = create(client, [cheap, dear], shipping_out=20.0)

    equal = {line["name"]: line["allocated_overhead"] for line in body["cards"]}
    assert equal["Common 1/10"] == equal["Alt Art 2/10"], "equal split, by definition"

    body = client.patch(
        f"/api/submissions/{body['id']}", json={"cost_allocation_method": "value_weighted"}
    ).json()
    weighted = {line["name"]: line["allocated_overhead"] for line in body["cards"]}
    assert weighted["Alt Art 2/10"] > weighted["Common 1/10"]
    assert body["allocation_method"] == "value_weighted"
    assert "declared value" in body["allocation_note"]


def test_your_declared_value_on_a_line_is_recorded_as_yours(client: TestClient):
    card = make_card(client, "Umbreon VMAX", "215/203", value=None)
    body = create(client, [card])
    line_id = body["cards"][0]["submission_card_id"]

    body = client.patch(
        f"/api/submissions/{body['id']}/cards/{line_id}", json={"declared_value": 250.0}
    ).json()
    line = body["cards"][0]
    assert line["declared_value"] == 250.0
    assert line["declared_value_source"] == "user"


# --- Lifecycle ---------------------------------------------------------------


def test_a_shipped_submission_will_not_let_its_cards_change(client: TestClient):
    cards = [make_card(client, f"Card {i}", f"{i}/10") for i in range(2)]
    body = create(client, cards)
    client.patch(f"/api/submissions/{body['id']}", json={"status": "shipped"})

    extra = make_card(client, "Late", "9/10")
    response = client.post(
        f"/api/submissions/{body['id']}/cards", json={"card_ids": [extra["id"]]}
    )
    assert response.status_code == 409
    assert "record, not a draft" in response.json()["error"]["message"]


def test_an_actual_grade_can_be_recorded_after_it_comes_back(client: TestClient):
    """Phase 8 learns from these, so they must be writable once the parcel returns."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    body = create(client, [card])
    client.patch(f"/api/submissions/{body['id']}", json={"status": "returned"})
    line_id = body["cards"][0]["submission_card_id"]

    body = client.patch(
        f"/api/submissions/{body['id']}/cards/{line_id}",
        json={"actual_grade": 9.5, "cert_number": "12345678", "status": "graded"},
    ).json()
    line = body["cards"][0]
    assert line["actual_grade"] == 9.5
    assert line["status"] == "graded"


def test_a_shipped_submission_cannot_be_deleted(client: TestClient):
    body = create(client, [make_card(client, "Umbreon VMAX", "215/203")])
    client.patch(f"/api/submissions/{body['id']}", json={"status": "shipped"})

    response = client.delete(f"/api/submissions/{body['id']}")
    assert response.status_code == 409
    assert "Cancel it instead" in response.json()["error"]["message"]


def test_a_draft_can_be_deleted(client: TestClient):
    body = create(client, [make_card(client, "Umbreon VMAX", "215/203")])
    assert client.delete(f"/api/submissions/{body['id']}").status_code == 204
    assert client.get(f"/api/submissions/{body['id']}").status_code == 404


def test_an_unknown_status_is_refused_rather_than_stored(client: TestClient):
    body = create(client, [make_card(client, "Umbreon VMAX", "215/203")])
    response = client.patch(f"/api/submissions/{body['id']}", json={"status": "posted"})
    assert response.status_code == 409
    assert "not a submission status" in response.json()["error"]["message"]


# --- Honesty -----------------------------------------------------------------


def test_an_empty_submission_reports_no_average_cost(client: TestClient):
    body = client.post(
        "/api/submissions", json={"company_id": company_id(client), "card_ids": []}
    ).json()
    assert body["card_count"] == 0
    assert body["cost_per_card"] is None, "an average of nothing is not zero"
    assert any("No cards in this submission" in item for item in body["blockers"])


def test_a_batch_short_of_its_tier_minimum_is_costed_and_flagged(client: TestClient):
    """It is not refused — you are allowed to build a parcel over several sittings."""
    cards = [make_card(client, f"Card {i}", f"{i}/30") for i in range(3)]
    body = create(client, cards, tier_id=tier_id(client, "CGC", "Bulk"))

    assert body["total_cost"] > 0, "still costed"
    bulk = next(group for group in body["tiers"] if group["tier_name"] == "Bulk")
    assert bulk["short_by"] > 0
    assert any("needs" in item for item in body["blockers"])


def test_a_card_over_its_tier_ceiling_is_named_on_its_own_line(client: TestClient):
    cheap = make_card(client, "Cheap", "1/10", value=50.0)
    dear = make_card(client, "Priceless", "2/10", value=100_000.0)
    body = create(client, [cheap, dear])

    line = next(item for item in body["cards"] if item["name"].startswith("Priceless"))
    assert any("ceiling" in blocker for blocker in line["blockers"])


def test_the_list_returns_what_was_created(client: TestClient):
    create(client, [make_card(client, "A", "1/1")], name="January bulk")
    create(client, [make_card(client, "B", "2/2")], name="Alt arts")

    rows = client.get("/api/submissions").json()
    assert len(rows) == 2
    assert {row["name"] for row in rows} == {"January bulk", "Alt arts"}


def test_an_unknown_submission_is_a_404(client: TestClient):
    assert client.get("/api/submissions/nope").status_code == 404
