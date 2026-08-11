"""Economics through the API, and what the evaluation envelope says once costs are real."""

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


def add_sale(client: TestClient, card_id: str, days: int, price: float, **kwargs) -> dict:
    payload = {"sale_date": (TODAY - timedelta(days=days)).isoformat(), "sale_price": price}
    payload.update(kwargs)
    response = client.post(f"/api/cards/{card_id}/market/sales", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def company_id(client: TestClient, code: str) -> str:
    return next(c["id"] for c in client.get("/api/grading/companies").json() if c["code"] == code)


def seed_market(client: TestClient, card_id: str) -> None:
    """Raw plus graded sales for two graders whose slabs sell very differently."""
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200 + index)
    for index in range(10):
        add_sale(
            client, card_id, days=index * 7, price=880,
            company_id=company_id(client, "PSA"), grade=10,
        )
        add_sale(
            client, card_id, days=index * 7, price=520,
            company_id=company_id(client, "CGC"), grade=10,
        )
        add_sale(
            client, card_id, days=index * 9, price=260,
            company_id=company_id(client, "ACE"), grade=10,
        )


def assess(client: TestClient, card_id: str) -> None:
    client.put(
        f"/api/cards/{card_id}/condition",
        json={
            "centering": {
                "front": {"left": 52, "right": 48, "top": 51, "bottom": 49},
                "back": {"left": 55, "right": 45, "top": 52, "bottom": 48},
            },
            "front": BLANK_FACE,
            "back": BLANK_FACE,
        },
    )


def options_block(client: TestClient, card_id: str, batch: int = 1) -> dict:
    return client.get(
        f"/api/cards/{card_id}/evaluation", params={"batch_size": batch}
    ).json()["grading_options"]


# --- Declared value ----------------------------------------------------------


def test_declared_value_appears_with_its_basis(client: TestClient, card: dict):
    seed_market(client, card["id"])
    assess(client, card["id"])
    block = options_block(client, card["id"])

    assert block["declared_value"] is not None
    assert block["declared_value_source"] == "system"
    assert block["declared_value_confidence"] in {"low", "medium", "high"}
    assert "Probability-weighted" in block["declared_value_basis"]


def test_your_declared_value_wins_and_is_stored_separately(client: TestClient, card: dict):
    seed_market(client, card["id"])
    assess(client, card["id"])
    suggested = options_block(client, card["id"])["declared_value"]

    client.patch(f"/api/cards/{card['id']}", json={"user_declared_value": 250.0})
    block = options_block(client, card["id"])

    assert block["declared_value"] == 250.0
    assert block["declared_value_source"] == "user"
    assert suggested != 250.0, "the suggestion was a different number"
    assert client.get(f"/api/cards/{card['id']}").json()["user_declared_value"] == 250.0


def test_a_declared_value_with_no_evidence_is_none_not_zero(client: TestClient):
    created = client.post("/api/cards", json={"name": "Sylveon VMAX", "set_code": "EVS"}).json()
    block = options_block(client, created["id"])
    assert block["declared_value"] is None
    assert block["status"] == "partial"
    assert "without a declared value" in block["reason"]


# --- Costs -------------------------------------------------------------------


def test_the_batch_changes_what_a_card_costs(client: TestClient, card: dict):
    seed_market(client, card["id"])
    assess(client, card["id"])

    alone = options_block(client, card["id"], batch=1)
    batched = options_block(client, card["id"], batch=25)

    def economy(block: dict) -> dict:
        return next(
            option
            for option in block["options"]
            if option["company_code"] == "CGC" and option["tier_name"] == "Economy"
        )

    assert economy(alone)["grading_fee"] == economy(batched)["grading_fee"]
    assert economy(alone)["allocated_overhead"] > economy(batched)["allocated_overhead"]
    assert economy(alone)["total_cost"] > economy(batched)["total_cost"]
    assert batched["assumed_batch_size"] == 25


def test_the_cost_shows_its_working(client: TestClient, card: dict):
    seed_market(client, card["id"])
    assess(client, card["id"])
    option = next(
        o for o in options_block(client, card["id"])["options"] if o["tier_name"] == "Economy"
    )

    parts = sum(
        filter(
            None,
            (
                option["grading_fee"],
                option["per_card_fees"],
                option["declared_value_fee"],
                option["allocated_overhead"],
            ),
        )
    )
    assert round(parts, 2) == option["total_cost"]


def test_a_tier_whose_ceiling_the_card_exceeds_says_so(client: TestClient, card: dict):
    seed_market(client, card["id"])
    assess(client, card["id"])
    block = options_block(client, card["id"])

    bulk = next(
        o for o in block["options"] if o["company_code"] == "CGC" and o["tier_name"] == "Bulk"
    )
    assert bulk["available"] is False
    assert any("ceiling" in reason for reason in bulk["blockers"])


# --- Net sale value ----------------------------------------------------------


def test_selling_raw_is_netted_so_grading_has_something_to_beat(client: TestClient, card: dict):
    seed_market(client, card["id"])
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()

    raw = body["raw"]
    assert raw["net_raw_sale_value"] is not None
    assert raw["net_raw_sale_value"] < raw["best_raw_value"], "fees and postage come off"


def test_a_slab_pays_graded_postage_and_nets_proportionally_less(client: TestClient, card: dict):
    seed_market(client, card["id"])
    block = options_block(client, card["id"])
    nets = {row["grade_label"]: row for row in block["net_values"]}

    assert nets["raw"]["postage_cost"] == 1.55
    assert nets["PSA 10"]["postage_cost"] == 5.50
    assert nets["PSA 10"]["net"] < nets["PSA 10"]["gross"]


# --- Best case, per company --------------------------------------------------


def test_the_best_case_never_mixes_one_graders_fee_with_anothers_slab_price(
    client: TestClient, card: dict
):
    """ACE is cheapest to grade, but an ACE 10 does not sell for PSA 10 money."""
    seed_market(client, card["id"])
    assess(client, card["id"])
    block = options_block(client, card["id"], batch=25)

    by_code = {row["company_code"]: row for row in block["best_case"]}
    for code, row in by_code.items():
        if row["best_grade_label"]:
            assert row["best_grade_label"].startswith(code)

    # The cheapest fee and the best outcome are different companies — which is
    # the whole reason the pairing has to be per company.
    cheapest_fee = min(
        (o for o in block["options"] if o["available"]), key=lambda o: o["total_cost"]
    )
    best_outcome = block["best_case"][0]
    assert cheapest_fee["company_code"] == "ACE"
    assert best_outcome["company_code"] == "CGC"
    assert best_outcome["upside_vs_raw"] > by_code["ACE"]["upside_vs_raw"]


def test_a_company_with_no_graded_sales_says_so_rather_than_borrowing_prices(
    client: TestClient, card: dict
):
    for index in range(20):
        add_sale(client, card["id"], days=index * 4, price=200)
    for index in range(10):
        add_sale(
            client, card["id"], days=index * 7, price=520,
            company_id=company_id(client, "CGC"), grade=10,
        )
    assess(client, card["id"])

    block = options_block(client, card["id"], batch=25)
    ace = next(row for row in block["best_case"] if row["company_code"] == "ACE")
    assert ace["best_net"] is None
    assert ace["reason"] == "No ACE sales stored, so a ACE slab cannot be priced."


def test_the_why_panel_names_the_grader_it_costed_against(client: TestClient, card: dict):
    seed_market(client, card["id"])
    assess(client, card["id"])
    body = client.get(
        f"/api/cards/{card['id']}/evaluation", params={"batch_size": 25}
    ).json()

    line = next(item for item in body["explanation"] if item["text"].startswith("Best case"))
    grader = line["text"].split()[2]
    assert f"after {grader} " in line["text"], "the fee and the slab price are the same grader"


# --- The envelope ------------------------------------------------------------


def test_the_envelope_shape_survives_the_economics_landing(client: TestClient, card: dict):
    seed_market(client, card["id"])
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()
    for block in (
        "raw", "condition", "grade_prediction", "market", "liquidity", "trend",
        "grading_options", "expected_outcomes", "recommendation",
    ):
        assert block in body
        assert "status" in body[block]


def test_expected_outcomes_still_names_the_phase_that_delivers_it(client: TestClient, card: dict):
    seed_market(client, card["id"])
    block = client.get(f"/api/cards/{card['id']}/evaluation").json()["expected_outcomes"]
    assert block["status"] == "not_implemented"
    assert block["phase"] == 5
