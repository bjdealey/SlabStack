"""The market routes, and what `evaluate_card` does once real sales exist.

The point of these tests is the seam: adding sales through the API has to change
what the decision envelope says, without changing its shape.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

TODAY = date.today()


def days_ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def add_sale(client: TestClient, card_id: str, days: int, price: float, **kwargs) -> dict:
    payload = {"sale_date": days_ago(days), "sale_price": price, **kwargs}
    response = client.post(f"/api/cards/{card_id}/market/sales", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def seed_raw_sales(client: TestClient, card_id: str, count: int = 12, price: float = 150.0) -> None:
    for index in range(count):
        add_sale(
            client,
            card_id,
            days=index * 5,
            price=price + index,
            listing_title="Umbreon VMAX Alt Art 215/203",
        )


# --- Sales -------------------------------------------------------------------


def test_a_card_with_no_sales_reports_an_empty_market(client: TestClient, card: dict):
    body = client.get(f"/api/cards/{card['id']}/market").json()
    assert body["sale_count"] == 0
    assert body["prices"] == []
    assert body["liquidity"]["band"] == "unknown"
    assert body["trend"]["direction"] == "insufficient_data"


def test_adding_a_sale_prices_the_card_immediately(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"])

    body = client.get(f"/api/cards/{card['id']}/market").json()
    assert body["sale_count"] == 12
    raw = next(row for row in body["prices"] if row["grade_label"] == "raw")
    assert raw["median"] is not None
    assert raw["sample_size"] == 12
    assert raw["confidence"] in {"medium", "high"}
    assert raw["quick_sale"] < raw["realistic_sale"]


def test_a_lot_listing_is_excluded_but_still_listed(client: TestClient, card: dict):
    sale = add_sale(
        client, card["id"], days=3, price=400.0, listing_title="Pokemon Job Lot 60 Cards"
    )
    assert sale["is_excluded"] is True
    assert sale["exclusion_reason"] == "lot_or_bundle"
    assert sale["excluded_by"] == "system"

    listed = client.get(f"/api/cards/{card['id']}/market/sales").json()
    assert len(listed) == 1

    counted = client.get(
        f"/api/cards/{card['id']}/market/sales", params={"include_excluded": False}
    ).json()
    assert counted == []


def test_an_exclusion_can_be_reversed_and_the_price_moves(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"], count=8, price=100.0)
    outlier = add_sale(
        client, card["id"], days=1, price=900.0, listing_title="Umbreon VMAX Alt Art bundle"
    )
    assert outlier["is_excluded"] is True

    before = client.get(f"/api/cards/{card['id']}/market").json()
    before_raw = next(row for row in before["prices"] if row["grade_label"] == "raw")

    response = client.put(
        f"/api/market/sales/{outlier['id']}/exclusion", json={"excluded": False}
    )
    assert response.status_code == 200
    assert response.json()["excluded_by"] == "user"

    after = client.get(f"/api/cards/{card['id']}/market").json()
    after_raw = next(row for row in after["prices"] if row["grade_label"] == "raw")
    assert after_raw["sample_size"] == before_raw["sample_size"] + 1


def test_filters_can_be_turned_off_for_a_sale_you_checked_yourself(client: TestClient, card: dict):
    sale = add_sale(
        client,
        card["id"],
        days=3,
        price=150.0,
        listing_title="Japanese Umbreon VMAX",
        apply_filters=False,
    )
    assert sale["is_excluded"] is False


def test_a_graded_sale_needs_a_company_and_a_grade_together(client: TestClient, card: dict):
    companies = client.get("/api/grading/companies").json()
    psa = next(company for company in companies if company["code"] == "PSA")

    response = client.post(
        f"/api/cards/{card['id']}/market/sales",
        json={"sale_date": days_ago(5), "sale_price": 900.0, "company_id": psa["id"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_grade"


def test_graded_sales_get_their_own_valuation_and_a_premium(client: TestClient, card: dict):
    companies = client.get("/api/grading/companies").json()
    psa = next(company for company in companies if company["code"] == "PSA")

    seed_raw_sales(client, card["id"], count=10, price=150.0)
    for index in range(10):
        add_sale(
            client,
            card["id"],
            days=index * 6,
            price=600.0 + index,
            company_id=psa["id"],
            grade=10,
        )

    body = client.get(f"/api/cards/{card['id']}/market").json()
    labels = {row["grade_label"] for row in body["prices"]}
    assert labels == {"raw", "PSA 10"}

    slab = next(row for row in body["prices"] if row["grade_label"] == "PSA 10")
    assert slab["premium_vs_raw_pct"] > 200
    assert slab["sample_size"] == 10


# --- CSV import --------------------------------------------------------------

CSV = """Date Sold,Sold For,Title,Item ID
{d0},£152.00,Umbreon VMAX Alt Art 215/203,111
{d1},£148.50,Umbreon VMAX Alt Art NM,112
{d2},£160.00,Pokemon Job Lot 40 Cards,113
{d3},£155.00,Japanese Umbreon VMAX Alt Art,114
{d4},not-a-price,Umbreon VMAX Alt Art,115
"""


def test_csv_import_reports_exactly_what_it_did(client: TestClient, card: dict):
    body = CSV.format(**{f"d{index}": days_ago(index * 7 + 1) for index in range(5)})
    response = client.post(
        f"/api/cards/{card['id']}/market/sales/import", json={"csv": body}
    )
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["imported"] == 4
    assert result["excluded"] == 2
    assert result["exclusions"] == {"lot_or_bundle": 1, "wrong_language": 1}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["line_number"] == 6
    assert result["prices"], "importing prices the card in the same request"


def test_reimporting_the_same_csv_does_not_double_the_sample(client: TestClient, card: dict):
    body = CSV.format(**{f"d{index}": days_ago(index * 7 + 1) for index in range(5)})
    client.post(f"/api/cards/{card['id']}/market/sales/import", json={"csv": body})
    second = client.post(
        f"/api/cards/{card['id']}/market/sales/import", json={"csv": body}
    ).json()

    assert second["imported"] == 0
    assert second["updated"] == 4
    assert len(client.get(f"/api/cards/{card['id']}/market/sales").json()) == 4


def test_an_unreadable_csv_fails_with_a_message_not_a_stack_trace(client: TestClient, card: dict):
    response = client.post(
        f"/api/cards/{card['id']}/market/sales/import", json={"csv": "colour,size\nred,big\n"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["imported"] == 0
    assert "sale_date" in result["errors"][0]["message"]


# --- Overrides and history ---------------------------------------------------


def test_a_user_value_sits_beside_the_computed_one(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"])
    prices = client.get(f"/api/cards/{card['id']}/market").json()["prices"]
    raw = next(row for row in prices if row["grade_label"] == "raw")

    response = client.put(
        f"/api/market/prices/{raw['id']}/override",
        json={"value": 250.0, "note": "Signed copy, sells higher."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_value"] == 250.0
    assert body["median"] == raw["median"], "the computed figure is not overwritten"


def test_recomputing_writes_a_price_history_point(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"])
    client.post(f"/api/cards/{card['id']}/market/recompute")

    history = client.get(f"/api/cards/{card['id']}/market/history").json()
    assert history
    raw = next(series for series in history if series["grade_label"] == "raw")
    assert len(raw["points"]) == 1
    assert raw["points"][0]["snapshot_date"] == TODAY.isoformat()


def test_correcting_a_cards_language_takes_its_sales_with_it(client: TestClient, card: dict):
    """Language is part of the identity, so editing it moves the card to a new key.

    Without following the sales, a corrected language would look like the whole
    market history vanishing.
    """
    sale = add_sale(
        client, card["id"], days=3, price=150.0, listing_title="Japanese Umbreon VMAX Alt Art"
    )
    assert sale["is_excluded"] is True, "wrong language for an English card"

    client.patch(f"/api/cards/{card['id']}", json={"language": "Japanese"})

    sales = client.get(f"/api/cards/{card['id']}/market/sales").json()
    assert len(sales) == 1, "the sale followed the card to its new identity"
    assert sales[0]["is_excluded"] is False, "and was re-judged against it"


def test_reclassify_is_idempotent(client: TestClient, card: dict):
    add_sale(client, card["id"], days=3, price=150.0, listing_title="Japanese Umbreon VMAX Alt Art")
    client.patch(f"/api/cards/{card['id']}", json={"language": "Japanese"})

    result = client.post(f"/api/cards/{card['id']}/market/reclassify").json()
    assert result["unchanged"] == 1
    assert result["kept"] == 0


def test_correcting_a_typo_keeps_the_market_history(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"])
    client.patch(f"/api/cards/{card['id']}", json={"card_number": "215/203 "})

    body = client.get(f"/api/cards/{card['id']}/market").json()
    assert body["sale_count"] == 12


# --- The evaluation envelope -------------------------------------------------


def test_evaluation_market_block_fills_in_once_sales_exist(client: TestClient, card: dict):
    before = client.get(f"/api/cards/{card['id']}/evaluation").json()
    assert before["market"]["status"] == "insufficient_data"
    assert before["market"]["phase"] == 3

    # Every third day, so all 25 land inside the 90-day valuation window.
    for index in range(25):
        add_sale(client, card["id"], days=index * 3, price=150.0 + index)
    after = client.get(f"/api/cards/{card['id']}/evaluation").json()

    assert after["market"]["status"] == "ok"
    assert after["market"]["raw"]["realistic_sale"] is not None
    assert after["market"]["raw"]["sample_size"] == 25
    assert after["market"]["raw"]["window_days"] == 90
    assert after["liquidity"]["status"] == "ok"
    assert after["liquidity"]["score"] is not None
    assert after["trend"]["direction"] != "insufficient_data"


def test_the_envelope_shape_survives_the_market_landing(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"])
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()
    for block in (
        "raw",
        "condition",
        "grade_prediction",
        "market",
        "liquidity",
        "trend",
        "grading_options",
        "expected_outcomes",
        "recommendation",
    ):
        assert block in body
        assert "status" in body[block]
    assert isinstance(body["explanation"], list)
    assert isinstance(body["blockers"], list)


def test_the_raw_value_comes_from_the_market_when_the_user_has_not_set_one(
    client: TestClient, card: dict
):
    seed_raw_sales(client, card["id"])
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()
    assert body["raw"]["raw_value_source"] == "market"
    assert body["raw"]["market_raw_value"] is not None


def test_a_thin_sample_is_partial_and_says_so(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"], count=3)
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()

    assert body["market"]["status"] == "partial"
    assert "Thin evidence" in body["market"]["reason"]
    assert body["data_confidence"] in {"none", "low"}


def test_sales_that_are_all_excluded_do_not_become_a_valuation(client: TestClient, card: dict):
    add_sale(client, card["id"], days=2, price=400.0, listing_title="Pokemon bundle job lot")
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()

    assert body["market"]["status"] == "insufficient_data"
    assert "excluded as non-comparable" in body["market"]["reason"]


def test_an_illiquid_card_is_named_as_a_blocker(client: TestClient, card: dict):
    for index in range(3):
        add_sale(client, card["id"], days=200 + index * 40, price=150.0)
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()

    assert body["liquidity"]["band"] in {"illiquid", "very_illiquid"}
    assert any("barely trades" in blocker for blocker in body["blockers"])


def test_two_copies_of_the_same_card_share_one_market_history(client: TestClient, card: dict):
    duplicate = client.post(
        "/api/cards",
        json={
            "name": "Umbreon VMAX",
            "set_code": "EVS",
            "card_number": "215/203",
            "variant": "Alternate Art",
            "language": "English",
        },
    ).json()

    seed_raw_sales(client, card["id"])
    body = client.get(f"/api/cards/{duplicate['id']}/market").json()
    assert body["sale_count"] == 12, "the second copy sees the first copy's comparables"


def test_the_why_panel_does_not_say_none_confidence(client: TestClient, card: dict):
    """'none confidence' is not English, and the raw value needs its currency."""
    for index in range(3):
        add_sale(client, card["id"], days=index * 20, price=150.0)
    body = client.get(f"/api/cards/{card['id']}/evaluation").json()

    raw_line = next(
        item for item in body["explanation"] if item["text"].startswith("Raw value")
    )
    assert "none confidence" not in raw_line["text"]
    assert "£" in raw_line["text"]


def test_the_dashboard_total_uses_market_values_where_it_has_them(client: TestClient, card: dict):
    """A card with comparables should be valued by the market, not by what it cost."""
    before = client.get("/api/collection/summary").json()["values"]
    assert before["known_raw_value"] == 185.0, "purchase price is the only figure so far"
    assert "no card has comparable sales yet" in before["values_reason"]

    seed_raw_sales(client, card["id"], count=12, price=300.0)
    after = client.get("/api/collection/summary").json()["values"]

    assert after["known_raw_value"] > 290
    assert after["cards_with_value"] == 1
    assert "market valuation for 1 card" in after["values_reason"]


def test_your_own_raw_estimate_still_outranks_the_market(client: TestClient, card: dict):
    seed_raw_sales(client, card["id"], count=12, price=300.0)
    client.patch(f"/api/cards/{card['id']}", json={"user_raw_value": 500.0})

    values = client.get("/api/collection/summary").json()["values"]
    assert values["known_raw_value"] == 500.0


def test_readiness_counts_cards_covered_not_sales_stored(client: TestClient, card: dict):
    """A hundred sales on one card does not make the other eleven analysable."""
    client.post("/api/cards", json={"name": "Rayquaza VMAX", "set_code": "EVS"})
    seed_raw_sales(client, card["id"], count=20)

    readiness = client.get("/api/collection/summary").json()["readiness"]
    market = next(item for item in readiness if item["key"] == "market_data")
    assert market["count"] == 1
    assert market["total"] == 2
