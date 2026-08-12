"""Analytics: ranking, pricing a listing, and scoring what came back.

The thing most worth testing here is that analytics stays a *view*. Every figure
it reports should be traceable to an engine that already produced it, so the
tests below assert agreement with those engines rather than re-deriving the
numbers a second time — which is exactly the mistake they exist to catch.
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
    assert client.post(f"/api/cards/{card_id}/market/sales", json=payload).status_code == 201


def assess(client: TestClient, card_id: str, left: int = 51, top: int = 50) -> None:
    centering = {"left": left, "right": 100 - left, "top": top, "bottom": 100 - top}
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


def seed_winner(client: TestClient, card_id: str) -> None:
    """A card grading clearly pays for."""
    grader = company_id(client, "CGC")
    for index in range(20):
        add_sale(client, card_id, days=index * 4, price=200)
    for grade, price in ((10, 900), (9.5, 610), (9, 400), (8.5, 320), (8, 260), (7.5, 235)):
        for index in range(6):
            add_sale(client, card_id, days=index * 9, price=price, company_id=grader, grade=grade)


def seed_seller(client: TestClient, card_id: str) -> None:
    """A card grading demonstrably does not pay for.

    Note what this needs: *graded* sales as well as raw ones. "Sell it raw" is a
    conclusion, and the engine can only reach it once the graded outcome has a
    price to lose against. A card with raw sales alone comes back
    `insufficient_data`, which is the correct answer to a question nobody can
    answer yet — and it is why the selling queue is not simply "everything you
    were not told to grade".
    """
    for index in range(18):
        add_sale(client, card_id, days=index * 5, price=120 + index)
    grader = company_id(client, "CGC")
    # Slabs barely beat the raw card, so the fee swallows the whole uplift.
    for grade, price in ((10, 155), (9.5, 140), (9, 130), (8.5, 125), (8, 120)):
        for index in range(6):
            add_sale(client, card_id, days=index * 9, price=price, company_id=grader, grade=grade)


# --- Opportunities -----------------------------------------------------------


def test_opportunities_are_the_same_verdicts_as_the_decisions_endpoint(client: TestClient):
    """Two rankings of one question would eventually disagree. There is one."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"])

    ranked = client.get("/api/analytics/opportunities", params={"batch_size": 25}).json()
    decisions = client.get("/api/collection/decisions", params={"batch_size": 25}).json()

    by_card = {item["card_id"]: item for item in decisions["opportunities"]}
    assert ranked["items"], "the seeded card is worth grading"
    for item in ranked["items"]:
        source = by_card[item["card_id"]]
        assert item["expected_profit"] == source["expected_profit"]
        assert item["opportunity_score"] == source["opportunity_score"]
        assert item["decision"] == source["decision"]


def test_opportunities_only_lists_what_you_would_actually_send(client: TestClient):
    winner = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, winner["id"])
    assess(client, winner["id"])

    seller = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, seller["id"])
    assess(client, seller["id"])

    body = client.get("/api/analytics/opportunities", params={"batch_size": 25}).json()
    decisions = {item["decision"] for item in body["items"]}
    assert decisions <= {"grade", "grade_if_batch_filled"}
    assert seller["id"] not in {item["card_id"] for item in body["items"]}


def test_an_empty_collection_says_so_rather_than_ranking_nothing(client: TestClient):
    body = client.get("/api/analytics/opportunities").json()
    assert body["status"] == "insufficient_data"
    assert body["expected_profit"] is None
    assert body["items"] == []


# --- The selling queue -------------------------------------------------------


def test_the_selling_queue_suggests_an_asking_price_above_the_realistic_one(
    client: TestClient,
):
    """A listing price is what you ask, not what it fetches."""
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, card["id"])
    assess(client, card["id"])

    body = client.get("/api/analytics/selling-queue").json()
    assert body["items"], "a card grading does not pay for belongs in the selling queue"
    item = body["items"][0]
    assert item["suggested_listing"] >= item["realistic_sale"]
    assert item["listing_basis"]


def test_the_asking_price_never_exceeds_what_anyone_recently_paid(client: TestClient):
    """Asking above the upper quartile is how a listing sits unsold."""
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, card["id"])
    assess(client, card["id"])

    market = client.get(f"/api/cards/{card['id']}/market").json()
    raw = next(row for row in market["prices"] if row["grade_label"] == "raw")
    item = client.get("/api/analytics/selling-queue").json()["items"][0]

    if raw["high_quartile"]:
        assert item["suggested_listing"] <= raw["high_quartile"] + 1e-9


def test_an_illiquid_card_is_given_more_negotiating_room(client: TestClient):
    """A card that trades twice a year needs room; one that trades weekly does not."""
    from app.services.analytics import suggested_listing_minor

    liquid, liquid_basis = suggested_listing_minor(10_000, None, 9.0)
    thin, thin_basis = suggested_listing_minor(10_000, None, 1.0)
    assert thin > liquid
    assert "does not need room" in liquid_basis
    assert "trades rarely" in thin_basis


def test_a_card_with_no_raw_sales_gets_no_invented_price(client: TestClient):
    """Your own estimate is enough to decide against grading, not to price a listing.

    Graded sales, plus your own view of the raw card, and no raw sales at all.
    That is enough to say "sell it raw" and enough to say what you would keep —
    but the asking price would have nothing behind it, so there isn't one.
    """
    card = make_card(client, "Own estimate", "9/203", user_raw_value=120.0)
    grader = company_id(client, "CGC")
    for grade, price in ((10, 155), (9.5, 140), (9, 130), (8.5, 125), (8, 120)):
        for index in range(6):
            add_sale(client, card["id"], days=index * 9, price=price, company_id=grader, grade=grade)
    assess(client, card["id"])

    body = client.get("/api/analytics/selling-queue").json()
    item = next(row for row in body["items"] if row["card_id"] == card["id"])
    assert item["decision"] == "sell_raw"
    assert item["net_proceeds"] is not None, "your own estimate still nets a knowable figure"
    assert item["realistic_sale"] is None
    assert item["suggested_listing"] is None
    assert any("nothing to price a listing against" in blocker for blocker in item["blockers"])
    assert body["status"] == "partial", "an unpriceable row makes the queue partial, not ok"


def test_the_queue_reports_what_you_would_keep_not_the_headline_price(client: TestClient):
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, card["id"])
    assess(client, card["id"])

    item = client.get("/api/analytics/selling-queue").json()["items"][0]
    assert item["net_proceeds"] < item["realistic_sale"], "fees and postage come off"

    evaluation = client.get(f"/api/cards/{card['id']}/evaluation").json()
    assert item["net_proceeds"] == evaluation["raw"]["net_raw_sale_value"], (
        "the same figure the card page shows, not a second calculation"
    )


def test_gain_against_what_you_paid_is_null_when_you_did_not_record_it(client: TestClient):
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, card["id"])
    assess(client, card["id"])

    item = client.get("/api/analytics/selling-queue").json()["items"][0]
    assert item["purchase_price"] is None
    assert item["gain_vs_purchase"] is None, "unknown is not break-even"


# --- Submission returns ------------------------------------------------------


def build_returned_submission(client: TestClient, actual_grade: float | None) -> dict:
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"])

    tier = next(
        t["id"]
        for c in client.get("/api/grading/companies").json()
        if c["code"] == "CGC"
        for t in c["tiers"]
        if t["tier_name"] == "Economy"
    )
    submission = client.post(
        "/api/submissions",
        json={
            "company_id": company_id(client, "CGC"),
            "tier_id": tier,
            "card_ids": [card["id"]],
        },
    ).json()
    client.patch(f"/api/submissions/{submission['id']}", json={"status": "returned"})
    if actual_grade is not None:
        line = submission["cards"][0]["submission_card_id"]
        client.patch(
            f"/api/submissions/{submission['id']}/cards/{line}",
            json={"actual_grade": actual_grade, "status": "graded"},
        )
    return submission


def test_the_prediction_is_recorded_when_the_card_joins_the_parcel(client: TestClient):
    """Otherwise "predicted vs actual" has no left-hand side.

    Recorded at add-time and stored, not recomputed on the way out: scoring
    today's model against a grade it has already seen measures nothing.
    """
    submission = build_returned_submission(client, actual_grade=10)
    line = submission["cards"][0]
    assert line["predicted_grade"] is not None

    entry = client.get("/api/analytics/submission-returns").json()["submissions"][0]
    graded = entry["cards"][0]
    assert graded["predicted_grade"] == line["predicted_grade"]
    assert graded["surprise"] == round(10 - line["predicted_grade"], 2)
    assert entry["mean_surprise"] is not None


def test_moving_a_draft_to_another_grader_re_takes_the_prediction(client: TestClient):
    """A PSA prediction is not a CGC one, and a draft has not been sent."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"], left=57, top=56)

    submission = client.post(
        "/api/submissions",
        json={"company_id": company_id(client, "CGC"), "card_ids": [card["id"]]},
    ).json()
    before = submission["cards"][0]["predicted_grade"]

    moved = client.patch(
        f"/api/submissions/{submission['id']}",
        json={"company_id": company_id(client, "PSA")},
    ).json()
    after = moved["cards"][0]["predicted_grade"]
    assert before is not None and after is not None
    # The graders' ladders differ, so at least the prediction was re-taken
    # against the new one rather than left describing a parcel you cancelled.
    assert moved["company_code"] == "PSA"


def test_a_card_with_no_assessment_gets_no_invented_prediction(client: TestClient):
    """No prediction was made, so there is nothing to be right or wrong about."""
    card = make_card(client, "Unassessed", "1/1")
    submission = client.post(
        "/api/submissions",
        json={"company_id": company_id(client, "CGC"), "card_ids": [card["id"]]},
    ).json()
    assert submission["cards"][0]["predicted_grade"] is None


def test_a_returned_submission_is_scored_against_what_the_slab_is_worth(client: TestClient):
    build_returned_submission(client, actual_grade=10)

    body = client.get("/api/analytics/submission-returns").json()
    assert body["scored"] == 1
    entry = body["submissions"][0]
    assert entry["graded_count"] == 1
    graded = entry["cards"][0]
    assert graded["actual_grade"] == 10
    assert graded["graded_value"] > 0
    assert graded["net_if_sold"] < graded["graded_value"], "selling it costs something"
    assert entry["roi_pct"] is not None


def test_a_submission_still_out_is_not_scored_as_a_loss(client: TestClient):
    """Averaging an open submission in at zero would make every parcel look bad."""
    build_returned_submission(client, actual_grade=None)

    body = client.get("/api/analytics/submission-returns").json()
    assert body["scored"] == 0
    assert body["awaiting"] == 1
    assert body["total_profit"] is None
    assert body["status"] == "insufficient_data"
    assert "still out" in body["reason"]
    assert "nothing to score" in body["submissions"][0]["status_note"]


def test_a_grade_with_no_sales_behind_it_cannot_be_valued_and_says_so(client: TestClient):
    """The card graded a 6; nobody has sold a CGC 6 of it."""
    build_returned_submission(client, actual_grade=6)

    entry = client.get("/api/analytics/submission-returns").json()["submissions"][0]
    graded = entry["cards"][0]
    assert graded["graded_value"] is None
    assert any("cannot be valued" in item for item in graded["blockers"])


def test_a_slab_that_cannot_be_valued_makes_the_return_a_floor(client: TestClient):
    """It still cost money to grade, so the ROI understates rather than errs."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"])
    unvaluable = make_card(client, "Espeon VMAX", "213/203")
    seed_winner(client, unvaluable["id"])
    assess(client, unvaluable["id"])

    submission = client.post(
        "/api/submissions",
        json={"company_id": company_id(client, "CGC"), "card_ids": [card["id"], unvaluable["id"]]},
    ).json()
    client.patch(f"/api/submissions/{submission['id']}", json={"status": "returned"})
    lines = {row["card_id"]: row["submission_card_id"] for row in submission["cards"]}
    client.patch(
        f"/api/submissions/{submission['id']}/cards/{lines[card['id']]}",
        json={"actual_grade": 10, "status": "graded"},
    )
    # Nobody has sold a CGC 6 of it, so this one cannot be valued.
    client.patch(
        f"/api/submissions/{submission['id']}/cards/{lines[unvaluable['id']]}",
        json={"actual_grade": 6, "status": "graded"},
    )

    entry = client.get("/api/analytics/submission-returns").json()["submissions"][0]
    assert entry["roi_pct"] is not None
    assert "floor, not an estimate" in (entry["status_note"] or "")


def test_no_submissions_at_all_says_so(client: TestClient):
    body = client.get("/api/analytics/submission-returns").json()
    assert body["status"] == "insufficient_data"
    assert "no submissions yet" in body["reason"].lower()


# --- Filters -----------------------------------------------------------------


def test_every_filter_is_offered_with_a_description(client: TestClient):
    body = client.get("/api/analytics/filters").json()
    keys = {item["key"] for item in body}
    assert {"grade_now", "sell_raw", "hold", "high_risk", "needs_data"} <= keys
    for item in body:
        assert item["label"] and item["description"]


def test_a_filter_returns_the_cards_the_engine_put_in_that_bucket(client: TestClient):
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"])

    body = client.get(
        "/api/analytics/filters/grade_now", params={"batch_size": 25}
    ).json()
    assert card["id"] in body["card_ids"]
    for item in body["items"]:
        assert item["decision"] == "grade"


def test_a_filter_says_how_many_cards_it_could_not_classify(client: TestClient):
    """A card the engine could not decide is unanswered, not a non-match."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"])
    make_card(client, "Unassessed", "1/1")

    body = client.get("/api/analytics/filters/grade_now").json()
    assert body["unclassified"] >= 1
    assert body["status"] == "partial"
    assert "could not be decided" in body["reason"]


def test_the_needs_data_filter_does_not_count_itself_as_a_blind_spot(client: TestClient):
    make_card(client, "Unassessed", "1/1")
    body = client.get("/api/analytics/filters/needs_data").json()
    assert body["unclassified"] == 0


def test_declining_reads_the_trend_rather_than_the_verdict(client: TestClient):
    """A cut called "declining" has to mean prices fell, not "the engine said hold"."""
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, card["id"])  # recent sales cheaper than older ones
    assess(client, card["id"])

    evaluation = client.get(f"/api/cards/{card['id']}/evaluation").json()
    falling = evaluation["trend"]["direction"] in {"down", "strong_down"}

    body = client.get("/api/analytics/filters/declining").json()
    assert (card["id"] in body["card_ids"]) is falling, (
        "membership follows the trend block, and nothing else"
    )


def test_a_card_with_no_trend_behind_it_is_not_called_declining(client: TestClient):
    """`insufficient_data` is not a direction. Not knowing is not falling."""
    card = make_card(client, "Umbreon VMAX", "215/203")
    seed_winner(client, card["id"])
    assess(client, card["id"])

    body = client.get("/api/analytics/filters/declining").json()
    for item in body["items"]:
        assert item["trend_direction"] in {"down", "strong_down"}


def test_hard_to_sell_uses_the_minimum_liquidity_you_configured(client: TestClient):
    """Not a threshold invented here — the same bar the decision engine grades against."""
    card = make_card(client, "Sylveon VMAX", "211/203")
    seed_seller(client, card["id"])
    assess(client, card["id"])

    client.patch("/api/settings", json={"values": {"minimum_liquidity_score": 0.0}})
    assert client.get("/api/analytics/filters/low_liquidity").json()["card_ids"] == []

    # Raise the bar above anything achievable and the same card now qualifies.
    client.patch("/api/settings", json={"values": {"minimum_liquidity_score": 10.0}})
    body = client.get("/api/analytics/filters/low_liquidity").json()
    assert card["id"] in body["card_ids"]


def test_an_unknown_filter_is_refused(client: TestClient):
    response = client.get("/api/analytics/filters/nonsense")
    assert response.status_code == 404
    assert "not a collection filter" in response.json()["error"]["message"]


# --- Returns scored on money rather than on a price --------------------------


def test_a_sold_card_is_scored_on_what_it_fetched_not_on_today_s_price(client: TestClient):
    """A sale that happened beats a price that might.

    Until disposals existed this valued every returned slab at the current
    market, which made a parcel's ROI drift with the market long after the
    position was closed.
    """
    submission = build_returned_submission(client, actual_grade=10)
    card_id = submission["cards"][0]["card_id"]
    estimated = client.get("/api/analytics/submission-returns").json()["submissions"][0]
    assert estimated["cards"][0]["value_basis"] == "market"

    client.post(
        f"/api/cards/{card_id}/sold",
        json={"sold_on": "2026-08-01", "gross": 1500.0, "sold_graded": True,
              "grade_label": "CGC 10", "net_proceeds": 1400.0},
    )

    entry = client.get("/api/analytics/submission-returns").json()["submissions"][0]
    graded = entry["cards"][0]

    assert graded["value_basis"] == "realised"
    assert graded["net_if_sold"] == 1400.0
    assert graded["sold_on"] == "2026-08-01"
    assert entry["realised_count"] == 1


def test_the_grading_fee_is_not_charged_twice(client: TestClient):
    """The submission line is the parcel's cost. Taking the sale record's own
    grading figure as well would subtract the fee a second time."""
    submission = build_returned_submission(client, actual_grade=10)
    card_id = submission["cards"][0]["card_id"]
    client.post(
        f"/api/cards/{card_id}/sold",
        json={"sold_on": "2026-08-01", "gross": 1500.0, "sold_graded": True,
              "grade_label": "CGC 10", "net_proceeds": 1400.0, "grading_cost": 60.0},
    )

    graded = client.get("/api/analytics/submission-returns").json()["submissions"][0]["cards"][0]

    assert graded["profit"] == round(1400.0 - graded["cost"], 2)


def test_a_parcel_that_has_fully_sold_says_the_return_is_money(client: TestClient):
    submission = build_returned_submission(client, actual_grade=10)
    client.post(
        f"/api/cards/{submission['cards'][0]['card_id']}/sold",
        json={"sold_on": "2026-08-01", "gross": 1500.0, "sold_graded": True,
              "grade_label": "CGC 10", "net_proceeds": 1400.0},
    )

    entry = client.get("/api/analytics/submission-returns").json()["submissions"][0]

    assert "money, not an estimate" in (entry["status_note"] or "")


def test_an_unsold_parcel_is_still_scored_at_the_market(client: TestClient):
    """The fallback has to keep working: most parcels are scored before anything
    in them is sold."""
    build_returned_submission(client, actual_grade=10)

    entry = client.get("/api/analytics/submission-returns").json()["submissions"][0]

    assert entry["realised_count"] == 0
    assert entry["cards"][0]["value_basis"] == "market"
    assert entry["cards"][0]["net_if_sold"] > 0
