"""The decision through the API, and across the collection.

The point of these is the seam: the recommendation the card page shows has to
be derived from the same numbers the rest of the page is already showing, and
it has to stay honest when the evidence is thin.
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


def assess(client: TestClient, card_id: str, *, pristine: bool = True) -> None:
    centering = (
        {"left": 51, "right": 49, "top": 50, "bottom": 50}
        if pristine
        else {"left": 68, "right": 32, "top": 60, "bottom": 40}
    )
    client.put(
        f"/api/cards/{card_id}/condition",
        json={
            "centering": {"front": centering, "back": centering},
            "front": BLANK_FACE,
            "back": BLANK_FACE,
        },
    )


def seed_full_ladder(client: TestClient, card_id: str, code: str = "CGC") -> None:
    """Raw sales plus every grade the model is likely to predict.

    Full coverage on purpose: a thinly-priced card is a different test.
    """
    grader = company_id(client, code)
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200 + index)
    for grade, price in ((10.0, 900), (9.5, 620), (9.0, 400), (8.5, 320), (8.0, 260), (7.5, 230)):
        for index in range(6):
            add_sale(
                client, card_id, days=index * 9, price=price,
                company_id=grader, grade=grade,
            )


def seed_top_grade_only(client: TestClient, card_id: str) -> None:
    """Raw sales, and graded sales for the top grade and nothing else."""
    grader = company_id(client, "CGC")
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200)
    for index in range(6):
        add_sale(client, card_id, days=index * 9, price=900, company_id=grader, grade=10)


def evaluate(client: TestClient, card_id: str, batch: int = 25) -> dict:
    return client.get(
        f"/api/cards/{card_id}/evaluation", params={"batch_size": batch}
    ).json()


# --- The recommendation ------------------------------------------------------


def test_a_well_evidenced_profitable_card_is_a_grade(client: TestClient, card: dict):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    body = evaluate(client, card["id"])

    recommendation = body["recommendation"]
    assert recommendation["status"] == "ok"
    assert recommendation["decision"] == "grade"
    assert recommendation["company_code"] == "CGC"
    assert recommendation["expected_profit"] > 0
    assert recommendation["opportunity_score"] > 50
    assert set(recommendation["score_parts"]) == {
        "profitability", "grade_probability", "liquidity", "trend", "risk"
    }


def test_the_recommendation_shows_what_it_is_beating(client: TestClient, card: dict):
    """Profit is measured against selling raw, so the raw figure has to be visible."""
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    body = evaluate(client, card["id"])

    recommendation = body["recommendation"]
    assert recommendation["net_raw_alternative"] == body["raw"]["net_raw_sale_value"]
    assert recommendation["grading_cost"] is not None
    assert recommendation["downside"] is not None
    assert recommendation["upside"] > recommendation["downside"]


def test_expected_outcomes_lists_every_grade_with_its_profit(client: TestClient, card: dict):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    block = evaluate(client, card["id"])["expected_outcomes"]

    assert block["status"] == "ok"
    leader = block["outcomes"][0]
    assert leader["coverage"] == 1.0
    assert leader["rows"], "the per-grade table is the working behind the expectation"
    for row in leader["rows"]:
        assert row["label"].startswith(leader["company_code"])


def test_a_thinly_priced_card_says_what_is_missing_rather_than_blaming_the_card(
    client: TestClient, card: dict
):
    """Only the top grade has ever sold, and this card will not get the top grade.

    Every priced outcome is profitable, so the honest answer is not "this card
    is a bad bet" — it is "nobody has sold the grades it will actually get".
    """
    seed_top_grade_only(client, card["id"])
    assess(client, card["id"], pristine=False)

    body = evaluate(client, card["id"])
    assert body["recommendation"]["decision"] != "grade"
    assert any("likely outcomes have no CGC price" in item for item in body["blockers"])

    reasons = [item["text"] for item in body["recommendation"]["reasons"]]
    assert any("cannot be confirmed" in text for text in reasons)
    assert any("Add CGC" in text for text in reasons), "it names the sales that would settle it"

    outcomes = body["expected_outcomes"]
    assert outcomes["status"] == "partial"
    assert outcomes["outcomes"][0]["coverage"] < 1.0


def test_the_recommendation_carries_the_coverage_its_figures_assume(
    client: TestClient, card: dict
):
    """"+£555 profit" next to "sell raw" is only coherent if the 13% travels with it."""
    seed_top_grade_only(client, card["id"])
    assess(client, card["id"], pristine=False)

    recommendation = evaluate(client, card["id"])["recommendation"]
    assert recommendation["expected_profit"] is not None
    assert 0 < recommendation["coverage"] < 1
    leader = evaluate(client, card["id"])["expected_outcomes"]["outcomes"][0]
    assert recommendation["coverage"] == leader["coverage"]


def test_a_fully_priced_card_reports_full_coverage(client: TestClient, card: dict):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    assert evaluate(client, card["id"])["recommendation"]["coverage"] == 1.0


def test_the_outcome_table_shows_the_sale_price_as_well_as_what_you_keep(
    client: TestClient, card: dict
):
    """Without the gross next to the net, the selling costs are invisible."""
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])

    rows = evaluate(client, card["id"])["expected_outcomes"]["outcomes"][0]["rows"]
    priced = [row for row in rows if row["net_value"] is not None]
    assert priced, "the ladder was seeded, so something should be priced"
    for row in priced:
        assert row["gross_value"] is not None
        assert row["net_value"] < row["gross_value"], "fees and postage come off"


def test_a_card_ruled_out_on_price_is_not_told_to_go_and_find_sales(client: TestClient):
    """Nothing in the market data changes the answer for a £3.50 common."""
    created = client.post(
        "/api/cards", json={"name": "Bidoof", "set_code": "EVS", "user_raw_value": 3.5}
    ).json()
    assess(client, created["id"])

    body = evaluate(client, created["id"])
    assert body["recommendation"]["decision"] == "do_not_grade"
    assert not [item for item in body["blockers"] if item.startswith("Add graded sales")]


def test_a_market_valued_card_is_not_told_it_has_no_value(client: TestClient):
    """It contradicts the raw value shown two lines above it."""
    created = client.post(
        "/api/cards", json={"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"}
    ).json()
    seed_full_ladder(client, created["id"])
    assess(client, created["id"])

    texts = [item["text"] for item in evaluate(client, created["id"])["explanation"]]
    assert "No raw value recorded." not in texts, "the market has valued it"
    assert "Raw value is the market's, not yours." in texts


def test_a_card_nobody_has_valued_is_still_told_so(client: TestClient):
    created = client.post("/api/cards", json={"name": "Leafeon VMAX", "set_code": "EVS"}).json()
    texts = [item["text"] for item in evaluate(client, created["id"])["explanation"]]
    assert "No raw value recorded." in texts


def test_thin_coverage_lowers_the_confidence_rather_than_hiding_the_number(
    client: TestClient, card: dict
):
    """The one grade that *has* sold is real evidence — flag it, do not discard it."""
    seed_top_grade_only(client, card["id"])
    assess(client, card["id"], pristine=False)

    leader = evaluate(client, card["id"])["expected_outcomes"]["outcomes"][0]
    assert leader["expected_profit"] is not None, "a priced grade is still worth reporting"
    assert leader["confidence"] == "low"
    assert any("left out rather than counted as zero" in note for note in leader["notes"])


def test_probability_of_profit_never_exceeds_what_is_priced(client: TestClient, card: dict):
    """The unpriced grades count against it — unknown is not good news."""
    seed_top_grade_only(client, card["id"])
    assess(client, card["id"])

    leader = evaluate(client, card["id"])["expected_outcomes"]["outcomes"][0]
    assert leader["probability_of_profit"] <= leader["coverage"] + 1e-9


def test_a_card_worth_less_than_the_fee_is_never_graded(client: TestClient):
    created = client.post(
        "/api/cards", json={"name": "Bulk Common", "set_code": "EVS", "user_raw_value": 3.0}
    ).json()
    assess(client, created["id"])
    body = evaluate(client, created["id"])
    assert body["recommendation"]["decision"] == "do_not_grade"


def test_a_card_with_no_assessment_cannot_be_decided(client: TestClient, card: dict):
    seed_full_ladder(client, card["id"])
    body = evaluate(client, card["id"])
    assert body["recommendation"]["decision"] == "insufficient_data"
    assert body["expected_outcomes"]["status"] == "insufficient_data"
    assert "assess the card first" in body["expected_outcomes"]["reason"].lower()


def test_your_own_decision_still_overrules_the_engine(client: TestClient, card: dict):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    assert evaluate(client, card["id"])["recommendation"]["decision"] == "grade"

    client.patch(
        f"/api/cards/{card['id']}",
        json={"decision_override": "hold", "decision_override_reason": "Waiting for the set"},
    )
    recommendation = evaluate(client, card["id"])["recommendation"]
    assert recommendation["decision"] == "hold"
    assert recommendation["is_user_override"] is True


def test_risk_tolerance_changes_the_verdict_without_changing_the_numbers(
    client: TestClient, card: dict
):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"], pristine=False)

    balanced = evaluate(client, card["id"])["recommendation"]
    client.patch("/api/settings", json={"values": {"risk_tolerance": "conservative"}})
    cautious = evaluate(client, card["id"])["recommendation"]

    assert balanced["expected_profit"] == cautious["expected_profit"]
    assert cautious["downside"] <= balanced["downside"]


def seed_marginal(client: TestClient, card_id: str) -> None:
    """Slabs worth a little more than the raw card — enough to pay a batched fee, not a solo one."""
    grader = company_id(client, "CGC")
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200)
    for grade, price in ((10.0, 290), (9.5, 275), (9.0, 262), (8.5, 255), (8.0, 250), (7.5, 245)):
        for index in range(6):
            add_sale(client, card_id, days=index * 9, price=price, company_id=grader, grade=grade)


def test_the_batch_can_turn_a_no_into_a_maybe(client: TestClient, card: dict):
    """A card can be unprofitable alone and worth grading in a submission."""
    seed_marginal(client, card["id"])
    assess(client, card["id"])

    alone = evaluate(client, card["id"], batch=1)["recommendation"]
    assert alone["decision"] == "grade_if_batch_filled"
    assert evaluate(client, card["id"], batch=25)["recommendation"]["decision"] == "grade"

    reason = next(item["text"] for item in alone["reasons"] if "Sending it alone" in item["text"])
    assert "does not clear your bar" in reason


def test_a_batched_quote_says_which_batch_it_is_quoting(client: TestClient, card: dict):
    """"Grade it in a submission, £20.20" is a lie if you read it as the solo price."""
    seed_marginal(client, card["id"])
    assess(client, card["id"])

    alone = evaluate(client, card["id"], batch=1)["recommendation"]
    assert alone["assumed_batch_size"] > 1, "the quoted cost is not achievable on its own"

    options = client.get(
        f"/api/cards/{card['id']}/evaluation", params={"batch_size": 1}
    ).json()["grading_options"]["options"]
    solo = min(
        (o["total_cost"] for o in options if o["available"] and o["total_cost"] is not None),
    )
    assert alone["grading_cost"] < solo


# --- Across the collection ---------------------------------------------------


def test_the_collection_decisions_endpoint_reports_what_it_could_not_analyse(
    client: TestClient, card: dict
):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    client.post("/api/cards", json={"name": "Unassessed", "set_code": "EVS"})

    body = client.get("/api/collection/decisions", params={"batch_size": 25}).json()
    assert body["analysed"] == 1
    assert body["total_cards"] == 2
    assert body["skipped_not_ready"] == 1
    assert body["status"] == "partial"
    assert "condition assessment and comparable sales" in body["reason"]


def test_collection_totals_count_only_cards_it_would_actually_grade(
    client: TestClient, card: dict
):
    """Summing the profit of cards you were told not to grade describes no real plan."""
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])

    cheap = client.post(
        "/api/cards", json={"name": "Cheap", "set_code": "EVS", "user_raw_value": 4.0}
    ).json()
    assess(client, cheap["id"])
    for index in range(6):
        add_sale(client, cheap["id"], days=index * 5, price=4.0)

    body = client.get("/api/collection/decisions", params={"batch_size": 25}).json()
    assert body["counts"].get("grade") == 1
    graded = next(o for o in body["opportunities"] if o["decision"] == "grade")
    assert body["expected_profit"] == graded["expected_profit"]


def test_opportunities_are_ranked_best_first(client: TestClient, card: dict):
    seed_full_ladder(client, card["id"])
    assess(client, card["id"])
    second = client.post(
        "/api/cards", json={"name": "Rayquaza VMAX", "set_code": "EVS", "card_number": "218/203"}
    ).json()
    seed_full_ladder(client, second["id"])
    assess(client, second["id"], pristine=False)

    body = client.get("/api/collection/decisions", params={"batch_size": 25}).json()
    scores = [o["opportunity_score"] or -1 for o in body["opportunities"]]
    assert scores == sorted(scores, reverse=True)


def test_an_empty_collection_says_so_rather_than_reporting_zero(client: TestClient):
    body = client.get("/api/collection/decisions").json()
    assert body["status"] == "insufficient_data"
    assert body["expected_profit"] is None, "no data is not the same as no profit"
    assert "nothing to decide" in body["reason"]
