"""The evaluate_card envelope.

These tests pin the *shape* of the contract and, more importantly, that the
engine reports missing data instead of inventing numbers.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

BLOCKS = (
    "raw",
    "condition",
    "grade_prediction",
    "market",
    "liquidity",
    "trend",
    "grading_options",
    "expected_outcomes",
    "recommendation",
)


def test_envelope_has_every_block(client: TestClient, card: dict):
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()
    for block in BLOCKS:
        assert block in body, f"missing block: {block}"
        assert "status" in body[block]
    assert body["card_id"] == card["id"]
    assert body["engine_version"]


def test_raw_block_is_populated_in_phase_one(client: TestClient, card: dict):
    raw = client.get(f"/api/cards/{card['id']}/evaluation").json()["raw"]
    assert raw["status"] == "ok"
    assert raw["display_name"] == "Umbreon VMAX 215/203"
    assert raw["set_label"] == "Evolving Skies (EVS)"
    assert raw["purchase_price"] == 185.0


def test_user_value_overrides_market_but_both_are_kept(client: TestClient, card: dict):
    client.patch(f"/api/cards/{card['id']}", json={"user_raw_value": 200.0})
    raw = client.get(f"/api/cards/{card['id']}/evaluation").json()["raw"]
    assert raw["user_raw_value"] == 200.0
    assert raw["best_raw_value"] == 200.0
    assert raw["raw_value_source"] == "user_override"
    assert raw["purchase_price"] == 185.0


def test_no_data_yields_no_recommendation_rather_than_a_guess(client: TestClient, card: dict):
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()
    recommendation = body["recommendation"]

    assert recommendation["decision"] == "insufficient_data"
    assert recommendation["confidence"] == "none"
    assert recommendation["expected_profit"] is None
    assert recommendation["roi_pct"] is None
    assert body["market"]["status"] == "insufficient_data"
    assert body["data_confidence"] == "none"


def test_blockers_say_what_is_actually_missing(client: TestClient, card: dict):
    blockers = client.get(f"/api/cards/{card['id']}/evaluation").json()["blockers"]
    assert any("condition" in blocker.lower() for blocker in blockers)
    assert any("sale" in blocker.lower() for blocker in blockers)


def test_condition_block_reflects_an_assessment(client: TestClient, card: dict):
    client.put(
        f"/api/cards/{card['id']}/condition",
        json={
            "front": {"creases": "moderate", "corner_tl": "none"},
            "centering": {"front": {"left": 50, "right": 50, "top": 50, "bottom": 50}},
        },
    )
    condition = client.get(f"/api/cards/{card['id']}/evaluation").json()["condition"]

    assert condition["status"] in {"ok", "partial"}
    assert condition["assessment_id"]
    assert any("creases" in defect for defect in condition["notable_defects"])


def test_incomplete_assessment_is_flagged_partial(client: TestClient, card: dict):
    client.put(f"/api/cards/{card['id']}/condition", json={"front": {"corner_tl": "none"}})
    condition = client.get(f"/api/cards/{card['id']}/evaluation").json()["condition"]
    assert condition["status"] == "partial"
    assert "assessment is filled in" in condition["reason"]


def test_unbuilt_blocks_name_the_phase_that_delivers_them(client: TestClient, card: dict):
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()
    assert body["market"]["phase"] == 3
    assert body["expected_outcomes"]["phase"] == 5
    assert body["market"]["reason"]


def test_grade_prediction_waits_for_an_assessment_not_a_phase(client: TestClient, card: dict):
    """It is built now, so an unassessed card is missing evidence, not features."""
    block = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]
    assert block["status"] == "not_assessed"
    assert block["phase"] is None
    assert block["reason"]


def test_grading_options_come_from_configuration(client: TestClient, card: dict):
    options = client.get(f"/api/cards/{card['id']}/evaluation").json()["grading_options"]["options"]
    available = [option for option in options if option["available"]]

    assert {option["company_code"] for option in available} == {"CGC", "ACE"}

    bulk = next(o for o in available if o["company_code"] == "CGC" and o["tier_name"] == "Bulk")
    assert bulk["grading_fee"] == 16.80
    assert bulk["minimum_cards"] == 25
    assert bulk["requires_batch"] is True

    # PSA ships with tier structure but no verified price, so it is offered as
    # unavailable with an actionable blocker rather than costed at zero.
    psa = next(o for o in options if o["company_code"] == "PSA")
    assert psa["available"] is False
    assert "pricing" in psa["blockers"][0].lower()


def test_user_override_wins_over_the_engine(client: TestClient, card: dict):
    client.patch(
        f"/api/cards/{card['id']}",
        json={"decision_override": "hold", "decision_override_reason": "Waiting for the set to settle"},
    )
    recommendation = client.get(f"/api/cards/{card['id']}/evaluation").json()["recommendation"]

    assert recommendation["decision"] == "hold"
    assert recommendation["is_user_override"] is True
    assert "Waiting for the set" in recommendation["reasons"][0]["detail"]


def test_explanation_is_always_present(client: TestClient, card: dict):
    explanation = client.get(f"/api/cards/{card['id']}/evaluation").json()["explanation"]
    assert explanation
    assert all(item["kind"] in {"pass", "warn", "fail", "info"} for item in explanation)


def test_explanation_notices_missing_photographs(client: TestClient, card: dict, sample_image: bytes):
    before = client.get(f"/api/cards/{card['id']}/evaluation").json()["explanation"]
    assert any("photograph" in item["text"] and item["kind"] == "warn" for item in before)

    for side in ("front", "back"):
        client.post(
            f"/api/cards/{card['id']}/images",
            files={"files": (f"{side}.jpg", sample_image, "image/jpeg")},
            data={"side": side},
        )
    after = client.get(f"/api/cards/{card['id']}/evaluation").json()["explanation"]
    assert any("Front and back photographs" in item["text"] for item in after)
