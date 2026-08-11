"""Condition scoring and assessment storage."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.enums import Severity
from app.models import ConditionAssessment
from app.services.condition_service import (
    centering_face_score,
    overall_condition_score,
    recompute_scores,
)


class TestCentering:
    def test_perfect_centering_scores_ten(self):
        assert centering_face_score(50, 50, 50, 50, is_front=True) == 10.0

    def test_symmetric_inputs_score_identically(self):
        assert centering_face_score(48, 52, 50, 50, is_front=True) == centering_face_score(
            52, 48, 50, 50, is_front=True
        )

    def test_worse_axis_decides_the_face(self):
        # Excellent left/right does not rescue poor top/bottom.
        assert centering_face_score(50, 50, 35, 65, is_front=True) < 8.5

    def test_back_is_judged_more_leniently(self):
        front = centering_face_score(40, 60, 50, 50, is_front=True)
        back = centering_face_score(40, 60, 50, 50, is_front=False)
        assert back > front

    def test_units_do_not_matter(self):
        # Millimetres and percentages give the same answer: only the ratio counts.
        assert centering_face_score(4.8, 5.2, 5, 5, is_front=True) == centering_face_score(
            48, 52, 50, 50, is_front=True
        )

    def test_missing_measurements_return_none(self):
        assert centering_face_score(None, None, None, None, is_front=True) is None

    @pytest.mark.parametrize("worse,better", [((30, 70), (45, 55)), ((20, 80), (35, 65))])
    def test_monotonic(self, worse, better):
        assert centering_face_score(*worse, 50, 50, is_front=True) < centering_face_score(
            *better, 50, 50, is_front=True
        )


class TestSubScores:
    def _assessment(self, **overrides) -> ConditionAssessment:
        assessment = ConditionAssessment(card_id="x")
        for face in ("front", "back"):
            for field in (
                "corner_tl", "corner_tr", "corner_bl", "corner_br", "edge_condition",
                "surface_condition", "holo_condition", "scratches", "print_lines", "silvering",
                "whitening", "dents", "dimpling", "creases", "staining", "misc_defects",
            ):
                setattr(assessment, f"{face}_{field}", Severity.NONE.value)
        for key, value in overrides.items():
            setattr(assessment, key, value)
        return assessment

    def test_flawless_card_scores_ten(self):
        assessment = recompute_scores(self._assessment())
        assert assessment.corners_score == 10.0
        assert assessment.edges_score == 10.0
        assert assessment.surface_score == 10.0

    def test_worst_defect_dominates(self):
        one_severe = recompute_scores(self._assessment(front_corner_tl=Severity.SEVERE.value))
        four_minor = recompute_scores(
            self._assessment(
                front_corner_tl=Severity.MINOR.value,
                front_corner_tr=Severity.MINOR.value,
                front_corner_bl=Severity.MINOR.value,
                front_corner_br=Severity.MINOR.value,
            )
        )
        assert one_severe.corners_score < four_minor.corners_score

    def test_additional_defects_still_cost_something(self):
        single = recompute_scores(self._assessment(front_scratches=Severity.MINOR.value))
        double = recompute_scores(
            self._assessment(
                front_scratches=Severity.MINOR.value, back_scratches=Severity.MINOR.value
            )
        )
        assert double.surface_score < single.surface_score

    def test_scores_never_go_negative(self):
        assessment = self._assessment()
        for face in ("front", "back"):
            for field in ("surface_condition", "scratches", "creases", "staining", "dents"):
                setattr(assessment, f"{face}_{field}", Severity.SEVERE.value)
        assert recompute_scores(assessment).surface_score == 0.0

    def test_unassessed_fields_are_excluded_not_assumed_perfect(self):
        assessment = ConditionAssessment(card_id="x")  # everything unknown
        recompute_scores(assessment)
        assert assessment.corners_score is None
        assert assessment.completeness == 0.0

    def test_completeness_tracks_answered_fields(self):
        assessment = recompute_scores(self._assessment())
        assert assessment.completeness == pytest.approx(32 / 40)

        with_centering = self._assessment(
            front_centering_left=48, front_centering_right=52,
            front_centering_top=50, front_centering_bottom=50,
            back_centering_left=47, back_centering_right=53,
            back_centering_top=50, back_centering_bottom=50,
        )
        assert recompute_scores(with_centering).completeness == 1.0

    def test_overall_score_ignores_missing_components(self):
        assessment = recompute_scores(self._assessment())
        assert overall_condition_score(assessment) == 10.0


class TestConditionApi:
    def test_put_and_get(self, client: TestClient, card: dict):
        payload = {
            "centering": {
                "front": {"left": 48, "right": 52, "top": 51, "bottom": 49},
                "back": {"left": 47, "right": 53, "top": 50, "bottom": 50},
            },
            "front": {
                "corner_tl": "none", "corner_tr": "none", "corner_bl": "minor", "corner_br": "none",
                "edge_condition": "none", "surface_condition": "none", "holo_condition": "none",
                "scratches": "minor", "print_lines": "none", "silvering": "none",
                "whitening": "none", "dents": "none", "dimpling": "none", "creases": "none",
                "staining": "none", "misc_defects": "none",
                "defect_notes": {"scratches": "One hairline under direct light"},
            },
            "back": {
                "corner_tl": "none", "corner_tr": "none", "corner_bl": "none", "corner_br": "none",
                "edge_condition": "minor", "surface_condition": "none", "holo_condition": "none",
                "scratches": "none", "print_lines": "none", "silvering": "none",
                "whitening": "minor", "dents": "none", "dimpling": "none", "creases": "none",
                "staining": "none", "misc_defects": "none",
            },
            "notes": "Pack fresh",
        }
        response = client.put(f"/api/cards/{card['id']}/condition", json=payload)
        assert response.status_code == 200
        body = response.json()

        assert body["scores"]["centering_front"] is not None
        assert body["scores"]["corners"] < 10
        assert body["scores"]["completeness"] == 1.0
        assert body["front"]["defect_notes"]["scratches"].startswith("One hairline")

        fetched = client.get(f"/api/cards/{card['id']}/condition").json()
        assert fetched["id"] == body["id"]

    def test_reassessment_keeps_history(self, client: TestClient, card: dict):
        for _ in range(2):
            client.put(f"/api/cards/{card['id']}/condition", json={"notes": "look again"})
        history = client.get(f"/api/cards/{card['id']}/condition/history").json()
        assert len(history) == 2
        assert sum(1 for item in history if item["is_current"]) == 1

    def test_no_assessment_is_a_404_not_an_empty_object(self, client: TestClient, card: dict):
        assert client.get(f"/api/cards/{card['id']}/condition").status_code == 404

    def test_invalid_severity_rejected(self, client: TestClient, card: dict):
        response = client.put(
            f"/api/cards/{card['id']}/condition", json={"front": {"corner_tl": "catastrophic"}}
        )
        assert response.status_code == 422

    def test_centering_must_be_given_in_pairs(self, client: TestClient, card: dict):
        response = client.put(
            f"/api/cards/{card['id']}/condition", json={"centering": {"front": {"left": 48}}}
        )
        assert response.status_code == 422

    def test_grade_prediction_needs_an_assessment_first(self, client: TestClient, card: dict):
        response = client.post(f"/api/cards/{card['id']}/grade-prediction")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "no_assessment"
