"""Marking the model, and correcting it — carefully.

The tests that matter most here are the ones about restraint. Scoring a
prediction is easy arithmetic; the hard part is refusing to learn from four
cards, refusing to mark a prediction that was never made, and refusing to score
a distribution recomputed after the answer was known. Those three are what stop
this feature quietly making the model worse.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
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


def make_card(client: TestClient, name: str, number: str, **kwargs) -> dict:
    return client.post(
        "/api/cards", json={"name": name, "set_code": "EVS", "card_number": number, **kwargs}
    ).json()


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


def send_and_grade(
    client: TestClient,
    grades: list[float],
    *,
    code: str = "CGC",
    left: int = 51,
    assess_cards: bool = True,
) -> dict:
    """Send one parcel of len(grades) cards and record what came back."""
    card_ids = []
    for index, _ in enumerate(grades):
        card = make_card(client, f"Umbreon VMAX {index}", f"{200 + index}/203")
        if assess_cards:
            assess(client, card["id"], left=left)
        card_ids.append(card["id"])

    submission = client.post(
        "/api/submissions",
        json={"company_id": company_id(client, code), "card_ids": card_ids},
    ).json()
    client.patch(f"/api/submissions/{submission['id']}", json={"status": "returned"})

    for line, grade in zip(submission["cards"], grades, strict=True):
        client.patch(
            f"/api/submissions/{submission['id']}/cards/{line['submission_card_id']}",
            json={"actual_grade": grade, "status": "graded"},
        )
    return submission


# --- The Brier score ---------------------------------------------------------


def test_a_perfect_confident_prediction_scores_zero():
    from app.services.calibration import brier_score

    assert brier_score({"10": 1.0}, 10) == 0.0


def test_confidence_in_the_wrong_answer_costs_more_than_hedging():
    """Being 95% sure and wrong must hurt more than being 40% sure and wrong."""
    from app.services.calibration import brier_score

    confident = brier_score({"10": 0.95, "9": 0.05}, 9)
    hedged = brier_score({"10": 0.4, "9": 0.6}, 9)
    assert confident > hedged


def test_the_score_marks_the_whole_distribution_not_just_the_mode():
    """Two predictions with the same most-likely grade can be very different."""
    from app.services.calibration import brier_score

    sure = brier_score({"10": 0.9, "9.5": 0.1}, 10)
    unsure = brier_score({"10": 0.4, "9.5": 0.35, "9": 0.25}, 10)
    assert sure < unsure


def test_a_grade_the_model_ruled_out_entirely_is_a_maximal_miss():
    """Zero probability on what actually happened is the worst kind of wrong."""
    from app.services.calibration import brier_score

    ruled_out = brier_score({"10": 0.6, "9.5": 0.4}, 8)
    merely_unlikely = brier_score({"10": 0.6, "9.5": 0.35, "8": 0.05}, 8)
    assert ruled_out > merely_unlikely


def test_nothing_to_mark_scores_nothing_rather_than_badly():
    from app.services.calibration import brier_score

    assert brier_score(None, 10) is None
    assert brier_score({"10": 1.0}, None) is None
    assert brier_score({}, 10) is None


# --- Recording ---------------------------------------------------------------


def test_recording_a_grade_writes_a_scoreable_result(client: TestClient):
    send_and_grade(client, [10])

    body = client.get("/api/analytics/accuracy").json()
    assert body["scored"] == 1
    result = body["results"][0]
    assert result["actual_grade"] == 10
    assert result["predicted_grade"] is not None
    assert result["brier"] is not None


def test_correcting_a_mistyped_grade_corrects_its_score(client: TestClient):
    """Re-recording updates the row rather than stacking a second one."""
    submission = send_and_grade(client, [9])
    line = submission["cards"][0]["submission_card_id"]

    client.patch(
        f"/api/submissions/{submission['id']}/cards/{line}",
        json={"actual_grade": 10},
    )

    body = client.get("/api/analytics/accuracy").json()
    assert body["scored"] == 1, "one card, one result"
    assert body["results"][0]["actual_grade"] == 10


def test_a_card_sent_without_an_assessment_cannot_be_marked(client: TestClient):
    """No prediction was made, so there is nothing to be right or wrong about."""
    send_and_grade(client, [10], assess_cards=False)

    body = client.get("/api/analytics/accuracy").json()
    assert body["scored"] == 0
    assert body["awaiting"] == 1
    assert body["status"] == "insufficient_data"


def test_unscoreable_cards_are_counted_rather_than_dropped(client: TestClient):
    """A short list must never be mistaken for a complete one."""
    send_and_grade(client, [10])
    send_and_grade(client, [9], assess_cards=False)

    body = client.get("/api/analytics/accuracy").json()
    assert body["scored"] == 1
    assert body["awaiting"] == 1
    assert body["status"] == "partial"
    assert "cannot be marked" in body["reason"]


# --- The report --------------------------------------------------------------


def test_the_bias_is_signed_so_its_direction_is_readable(client: TestClient):
    """Positive means cards come back better than predicted."""
    # A pristine card predicted high, coming back at 8 every time.
    send_and_grade(client, [8, 8, 8, 8])

    company = client.get("/api/analytics/accuracy").json()["companies"][0]
    assert company["scored"] == 4
    assert company["mean_error"] is not None
    assert company["mean_error"] < 0, "graded worse than predicted, so the model reads generous"
    assert company["mean_absolute_error"] >= abs(company["mean_error"])


def test_each_grader_is_scored_on_its_own(client: TestClient):
    """A correction learned across two graders describes neither."""
    send_and_grade(client, [10, 10], code="CGC")
    send_and_grade(client, [8, 8], code="PSA")

    body = client.get("/api/analytics/accuracy").json()
    codes = {row["company_code"] for row in body["companies"]}
    assert codes == {"CGC", "PSA"}
    assert all(row["scored"] == 2 for row in body["companies"])


def test_the_calibration_curve_compares_predicted_rate_to_observed(client: TestClient):
    send_and_grade(client, [10, 10, 10, 9])

    company = client.get("/api/analytics/accuracy").json()["companies"][0]
    bands = {band["grade"]: band for band in company["bands"]}
    assert bands[10]["actual_rate"] == 0.75, "three of four came back a 10"
    assert bands[10]["predicted_rate"] is not None
    # The gap is what the whole curve exists to show.
    assert bands[10]["gap_pct"] == pytest.approx(
        (bands[10]["actual_rate"] - bands[10]["predicted_rate"]) * 100, abs=0.05
    )


def test_the_headline_names_the_grader_and_the_grade(client: TestClient):
    """The one sentence worth reading, per the spec."""
    send_and_grade(client, [8, 8, 8, 8])

    company = client.get("/api/analytics/accuracy").json()["companies"][0]
    assert company["headline"]
    assert "CGC" in company["headline"]


def test_no_results_at_all_says_so(client: TestClient):
    body = client.get("/api/analytics/accuracy").json()
    assert body["status"] == "insufficient_data"
    assert body["scored"] == 0
    assert "No graded results recorded yet" in body["reason"]


# --- The correction ----------------------------------------------------------


def test_a_handful_of_results_is_measured_but_never_applied(client: TestClient):
    """The single most important restraint in this module."""
    send_and_grade(client, [8, 8, 8])

    body = client.get("/api/calibration").json()
    cgc = next(row for row in body["companies"] if row["company_code"] == "CGC")
    assert cgc["sample_size"] == 3
    assert cgc["grade_offset"] != 0, "the bias is still measured"
    assert cgc["applied"] is False, "and explicitly not acted on"
    assert "noise" in cgc["reason"]


def test_enough_results_turns_the_correction_on(client: TestClient):
    client.patch("/api/settings", json={"values": {"calibration_minimum_sample": 4}})
    send_and_grade(client, [8, 8, 8, 8, 8])

    cgc = next(
        row
        for row in client.get("/api/calibration").json()["companies"]
        if row["company_code"] == "CGC"
    )
    assert cgc["sample_size"] == 5
    assert cgc["applied"] is True
    assert cgc["confidence"] in {"low", "medium", "high"}


def test_the_correction_is_capped_however_big_the_measured_bias(client: TestClient):
    """A measured two-grade bias is a run of odd cards, not a real one."""
    client.patch(
        "/api/settings",
        json={"values": {"calibration_minimum_sample": 2, "calibration_max_offset": 0.5}},
    )
    # Pristine cards all coming back at 5 — a huge, implausible measured error.
    send_and_grade(client, [5, 5, 5, 5])

    cgc = next(
        row
        for row in client.get("/api/calibration").json()["companies"]
        if row["company_code"] == "CGC"
    )
    assert cgc["grade_offset"] == -0.5, "clamped to the configured maximum"


def test_switching_calibration_off_still_reports_the_measurement(client: TestClient):
    """Turning it off should not blind you to what it found."""
    client.patch(
        "/api/settings",
        json={"values": {"calibration_minimum_sample": 2, "calibration_enabled": False}},
    )
    send_and_grade(client, [8, 8, 8, 8])

    cgc = next(
        row
        for row in client.get("/api/calibration").json()["companies"]
        if row["company_code"] == "CGC"
    )
    assert cgc["applied"] is False
    assert cgc["grade_offset"] != 0
    assert "switched off" in cgc["reason"]


def test_a_grader_you_have_never_used_has_nothing_to_learn_from(client: TestClient):
    send_and_grade(client, [10])

    psa = next(
        row
        for row in client.get("/api/calibration").json()["companies"]
        if row["company_code"] == "PSA"
    )
    assert psa["sample_size"] == 0
    assert psa["applied"] is False
    assert psa["grade_offset"] == 0
    assert "nothing to learn from" in psa["reason"]


# --- Feeding it back ---------------------------------------------------------


def test_a_calibrated_prediction_moves_and_says_it_moved(client: TestClient):
    """The correction must be visible, not silent."""
    client.patch("/api/settings", json={"values": {"calibration_minimum_sample": 3}})

    card = make_card(client, "Test subject", "1/203")
    assess(client, card["id"])
    before = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]
    raw_likely = next(
        row["likely_grade"] for row in before["by_company"] if row["company_code"] == "CGC"
    )

    send_and_grade(client, [8, 8, 8, 8])

    after = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]
    cgc = next(row for row in after["by_company"] if row["company_code"] == "CGC")
    assert cgc["source"] == "calibrated"
    assert cgc["likely_grade"] <= raw_likely, "learned that cards come back worse than predicted"


def test_the_raw_model_is_kept_alongside_the_calibrated_one(client: TestClient):
    """Spec section 35: the adjustment is shown, never substituted silently."""
    client.patch("/api/settings", json={"values": {"calibration_minimum_sample": 3}})
    send_and_grade(client, [8, 8, 8, 8])

    card = make_card(client, "Test subject", "1/203")
    assess(client, card["id"])
    cgc = next(
        row
        for row in client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"][
            "by_company"
        ]
        if row["company_code"] == "CGC"
    )
    assert cgc["uncalibrated_likely_grade"] is not None
    assert cgc["calibration_offset"] is not None
    assert cgc["uncalibrated_probabilities"], "the raw distribution survives, not just the mode"


def test_below_the_threshold_predictions_are_untouched(client: TestClient):
    """No evidence, no adjustment — and the source says so."""
    send_and_grade(client, [8, 8])

    card = make_card(client, "Test subject", "1/203")
    assess(client, card["id"])
    cgc = next(
        row
        for row in client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"][
            "by_company"
        ]
        if row["company_code"] == "CGC"
    )
    assert cgc["source"] == "rules_engine"
    assert cgc["calibration_offset"] is None


def test_a_user_override_still_outranks_a_calibrated_prediction(client: TestClient):
    """Learned or not, the model does not overrule a number the user typed."""
    client.patch("/api/settings", json={"values": {"calibration_minimum_sample": 3}})
    send_and_grade(client, [8, 8, 8, 8])

    card = make_card(client, "Test subject", "1/203")
    assess(client, card["id"])
    client.put(
        f"/api/cards/{card['id']}/grade-prediction/override",
        json={
            "company_id": company_id(client, "CGC"),
            "probabilities": {"10": 0.8, "9.5": 0.2},
            "confidence": "high",
        },
    )
    cgc = next(
        row
        for row in client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"][
            "by_company"
        ]
        if row["company_code"] == "CGC"
    )
    assert cgc["is_user_override"] is True
    assert cgc["likely_grade"] == 10


def test_the_dates_recorded_come_from_the_parcel(client: TestClient):
    submission = send_and_grade(client, [10])
    client.patch(
        f"/api/submissions/{submission['id']}",
        json={"returned_at": (TODAY - timedelta(days=3)).isoformat()},
    )
    line = submission["cards"][0]["submission_card_id"]
    client.patch(
        f"/api/submissions/{submission['id']}/cards/{line}", json={"actual_grade": 10}
    )

    result = client.get("/api/analytics/accuracy").json()["results"][0]
    assert result["graded_at"] == (TODAY - timedelta(days=3)).isoformat()
