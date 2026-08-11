"""The grade probability model.

The model's job is to be *honestly uncertain*. Most of these tests are about
that: worse cards score lower, less-assessed cards get wider ranges, and nothing
ever claims a confident grade from evidence that does not support one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import PredictionKind, Severity
from app.models import ConditionAssessment, GradingCompany
from app.services import prediction_service as model
from app.services import settings_service
from app.services.condition_service import recompute_scores
from app.services.prediction_service import ModelParameters, NotEnoughAssessmentError

DEFECTS = (
    "corner_tl", "corner_tr", "corner_bl", "corner_br", "edge_condition", "surface_condition",
    "holo_condition", "scratches", "print_lines", "silvering", "whitening", "dents", "dimpling",
    "creases", "staining", "misc_defects",
)


def assessment(*, centering: bool = True, **overrides) -> ConditionAssessment:
    row = ConditionAssessment(card_id="card")
    for face in ("front", "back"):
        for field in DEFECTS:
            setattr(row, f"{face}_{field}", Severity.NONE.value)
        if centering:
            for edge in ("left", "right", "top", "bottom"):
                setattr(row, f"{face}_centering_{edge}", 50.0)
    for key, value in overrides.items():
        setattr(row, key, value)
    return recompute_scores(row)


@pytest.fixture
def params() -> ModelParameters:
    return ModelParameters()


@pytest.fixture
def db(seeded_db):
    """These tests drive the model directly, so they need the seeded rules."""
    return seeded_db


@pytest.fixture
def company(db) -> GradingCompany:
    return db.scalars(select(GradingCompany).where(GradingCompany.code == "CGC")).one()


@pytest.fixture
def rules(db, company: GradingCompany):
    return model.load_rules(db, company.id)


def predict(row, company, rules, params, **kwargs):
    return model.predict(row, company=company, rules=rules, params=params, **kwargs)


class TestDistribution:
    def test_probabilities_sum_to_one(self, company, rules, params):
        result = predict(assessment(), company, rules, params)
        assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=0.01)

    def test_a_flawless_card_is_most_likely_the_top_grade(self, company, rules, params):
        result = predict(assessment(), company, rules, params)
        assert result.likely_grade == 10
        assert result.confidence == "high"

    def test_but_never_a_certainty(self, company, rules, params):
        """Graders are not deterministic; a 100% claim would be a lie."""
        result = predict(assessment(), company, rules, params)
        assert result.probabilities["10"] < 0.95
        assert len(result.probabilities) > 1

    def test_worse_condition_grades_lower(self, company, rules, params):
        flawless = predict(assessment(), company, rules, params)
        scratched = predict(
            assessment(front_scratches=Severity.MINOR.value), company, rules, params
        )
        worse = predict(
            assessment(front_scratches=Severity.MODERATE.value), company, rules, params
        )
        assert flawless.likely_grade >= scratched.likely_grade >= worse.likely_grade
        assert flawless.base_grade > scratched.base_grade > worse.base_grade

    def test_one_bad_attribute_is_not_hidden_by_three_good_ones(self, company, rules, params):
        """A 62/38 front is not a 10 just because everything else is perfect."""
        result = predict(
            assessment(front_centering_left=62.0, front_centering_right=38.0),
            company,
            rules,
            params,
        )
        assert result.likely_grade <= 9
        assert result.probabilities.get("10", 0) < 0.2

    def test_worse_centering_grades_lower_still(self, company, rules, params):
        mild = predict(
            assessment(front_centering_left=58.0, front_centering_right=42.0), company, rules, params
        )
        severe = predict(
            assessment(front_centering_left=72.0, front_centering_right=28.0), company, rules, params
        )
        assert severe.likely_grade < mild.likely_grade


class TestUncertainty:
    def test_an_unfinished_assessment_widens_the_range(self, company, rules, params):
        complete = predict(assessment(), company, rules, params)
        partial = predict(assessment(centering=False), company, rules, params)
        assert partial.sigma > complete.sigma

    def test_and_lowers_confidence(self, company, rules, params):
        row = assessment(centering=False)
        for field in DEFECTS[4:]:
            setattr(row, f"front_{field}", Severity.UNKNOWN.value)
            setattr(row, f"back_{field}", Severity.UNKNOWN.value)
        recompute_scores(row)

        partial = predict(row, company, rules, params)
        assert partial.confidence in {"low", "none"}
        assert partial.grade_max - partial.grade_min > 1

    def test_disagreeing_subscores_widen_the_range(self, company, rules, params):
        """10/10/10/6 is less predictable than four matching scores."""
        even = predict(assessment(front_scratches=Severity.MINOR.value), company, rules, params)
        lopsided = predict(
            assessment(front_creases=Severity.MODERATE.value), company, rules, params
        )
        assert lopsided.sigma > even.sigma

    def test_an_empty_assessment_predicts_nothing(self, params, company, rules):
        """Rather than inventing a grade from no evidence."""
        blank = recompute_scores(ConditionAssessment(card_id="card"))
        with pytest.raises(NotEnoughAssessmentError):
            predict(blank, company, rules, params)

    def test_unanswered_fields_are_not_treated_as_perfect(self, company, rules, params):
        row = ConditionAssessment(card_id="card")
        for field in DEFECTS:
            setattr(row, f"front_{field}", Severity.NONE.value)
        recompute_scores(row)

        result = predict(row, company, rules, params)
        assert result.confidence != "high"


class TestRules:
    def test_a_crease_caps_the_grade(self, company, rules, params):
        result = predict(
            assessment(front_creases=Severity.SEVERE.value), company, rules, params
        )
        assert result.max_grade_cap == 3.0
        assert result.likely_grade <= 3
        assert all(float(grade) <= 3 for grade in result.probabilities)

    def test_the_strictest_cap_wins(self, company, rules, params):
        result = predict(
            assessment(
                front_creases=Severity.SEVERE.value,  # caps at 3
                front_whitening=Severity.SEVERE.value,  # caps at 8
            ),
            company,
            rules,
            params,
        )
        assert result.max_grade_cap == 3.0

    def test_caps_are_reported_not_just_applied(self, company, rules, params):
        result = predict(
            assessment(front_creases=Severity.MODERATE.value), company, rules, params
        )
        assert result.caps_applied
        assert result.caps_applied[0]["label"]
        assert any(item["kind"] == "fail" for item in result.explanation)

    def test_a_multiplier_dents_the_top_grade_without_wrecking_the_card(
        self, company, rules, params
    ):
        clean = predict(assessment(), company, rules, params)
        whitened = predict(
            assessment(front_whitening=Severity.MINOR.value), company, rules, params
        )
        assert whitened.probabilities.get("10", 0) < clean.probabilities["10"]
        assert whitened.likely_grade >= 8  # dented, not destroyed

    def test_unknown_severity_never_triggers_a_rule(self, company, rules, params):
        row = assessment()
        row.front_creases = Severity.UNKNOWN.value
        recompute_scores(row)
        assert predict(row, company, rules, params).max_grade_cap is None

    def test_a_corner_group_rule_matches_any_corner(self, company, rules, params):
        for corner in ("corner_tl", "corner_tr", "corner_bl", "corner_br"):
            result = predict(
                assessment(**{f"front_{corner}": Severity.SEVERE.value}), company, rules, params
            )
            assert result.max_grade_cap == 6.0, corner

    def test_deactivating_a_rule_stops_it_applying(self, db, company, params):
        from app.models import GradeRule

        rule = db.scalars(select(GradeRule).where(GradeRule.code == "crease_severe")).one()
        rule.active = False
        db.flush()

        result = predict(
            assessment(front_creases=Severity.SEVERE.value),
            company,
            model.load_rules(db, company.id),
            params,
        )
        assert result.max_grade_cap != 3.0


class TestCompanies:
    def test_half_grades_only_where_the_company_awards_them(self, db, params):
        row = assessment(front_scratches=Severity.MINOR.value)
        psa = db.scalars(select(GradingCompany).where(GradingCompany.code == "PSA")).one()
        cgc = db.scalars(select(GradingCompany).where(GradingCompany.code == "CGC")).one()

        psa_grades = predict(row, psa, model.load_rules(db, psa.id), params).probabilities
        cgc_grades = predict(row, cgc, model.load_rules(db, cgc.id), params).probabilities

        assert all(float(grade).is_integer() for grade in psa_grades)
        assert any(not float(grade).is_integer() for grade in cgc_grades)

    def test_strictness_shifts_the_estimate(self, db, company, rules, params):
        row = assessment(front_scratches=Severity.MINOR.value)
        neutral = predict(row, company, rules, params)

        company.strictness = -1.0
        db.flush()
        strict = predict(row, company, rules, params)

        assert strict.likely_grade < neutral.likely_grade

    def test_strictness_ships_neutral_for_every_company(self, db):
        """SlabStack makes no claim about who grades harder."""
        for row in db.scalars(select(GradingCompany)):
            assert row.strictness == 0.0

    def test_a_physical_prediction_ignores_the_grader(self, db, company, rules, params):
        row = assessment(front_scratches=Severity.MINOR.value)
        company.strictness = -1.5
        db.flush()

        physical = predict(row, company, rules, params, kind=PredictionKind.PHYSICAL.value)
        market = predict(row, company, rules, params, kind=PredictionKind.MARKET.value)
        assert physical.likely_grade > market.likely_grade


class TestApi:
    def _assess(self, client: TestClient, card_id: str, **front) -> None:
        payload = {
            "centering": {
                "front": {"left": 50, "right": 50, "top": 50, "bottom": 50},
                "back": {"left": 50, "right": 50, "top": 50, "bottom": 50},
            },
            "front": dict.fromkeys(DEFECTS, "none") | front,
            "back": dict.fromkeys(DEFECTS, "none"),
        }
        response = client.put(f"/api/cards/{card_id}/condition", json=payload)
        assert response.status_code == 200, response.text

    def test_running_the_model_stores_one_prediction_per_company(
        self, client: TestClient, card: dict
    ):
        self._assess(client, card["id"])
        response = client.post(f"/api/cards/{card['id']}/grade-prediction")
        assert response.status_code == 200, response.text

        rows = response.json()
        kinds = {row["kind"] for row in rows}
        assert kinds == {"physical", "market"}
        assert {row["company_code"] for row in rows if row["kind"] == "market"} == {"PSA", "CGC", "ACE"}
        assert all(row["probabilities"] for row in rows)

    def test_without_an_assessment_it_says_so(self, client: TestClient, card: dict):
        response = client.post(f"/api/cards/{card['id']}/grade-prediction")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "no_assessment"

    def test_rerunning_supersedes_the_previous_run(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        client.post(f"/api/cards/{card['id']}/grade-prediction")
        client.post(f"/api/cards/{card['id']}/grade-prediction")

        current = client.get(
            f"/api/cards/{card['id']}/grade-predictions", params={"current_only": True}
        ).json()
        assert len(current) == 4  # one physical plus three companies
        assert len(client.get(f"/api/cards/{card['id']}/grade-predictions").json()) == 8

    def test_an_override_replaces_the_model_for_that_company(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        companies = {c["code"]: c for c in client.get("/api/grading/companies").json()}

        response = client.put(
            f"/api/cards/{card['id']}/grade-prediction/override",
            json={
                "company_id": companies["CGC"]["id"],
                "probabilities": {"10": 0.2, "9": 0.8},
                "confidence": "medium",
                "notes": "Seen it in hand; the centering is worse than it photographs.",
            },
        )
        assert response.status_code == 200
        assert response.json()["source"] == "user_override"

        block = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]
        cgc = next(item for item in block["by_company"] if item["company_code"] == "CGC")
        assert cgc["is_user_override"] is True
        assert cgc["likely_grade"] == 9

        # The other companies still come from the model.
        others = [item for item in block["by_company"] if item["company_code"] != "CGC"]
        assert all(item["is_user_override"] is False for item in others)

    def test_rerunning_the_model_leaves_an_override_alone(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        companies = {c["code"]: c for c in client.get("/api/grading/companies").json()}
        client.put(
            f"/api/cards/{card['id']}/grade-prediction/override",
            json={
                "company_id": companies["CGC"]["id"],
                "probabilities": {"9": 1.0},
                "confidence": "high",
            },
        )
        client.post(f"/api/cards/{card['id']}/grade-prediction")

        block = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]
        cgc = next(item for item in block["by_company"] if item["company_code"] == "CGC")
        assert cgc["is_user_override"] is True

    def test_probabilities_must_sum_to_one(self, client: TestClient, card: dict):
        response = client.put(
            f"/api/cards/{card['id']}/grade-prediction/override",
            json={"probabilities": {"10": 0.3, "9": 0.3}, "confidence": "low"},
        )
        assert response.status_code == 422


class TestEvaluationBlock:
    def _assess(self, client: TestClient, card_id: str, **front) -> None:
        TestApi()._assess(client, card_id, **front)

    def test_the_block_is_populated_once_a_card_is_assessed(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        block = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]

        assert block["status"] == "ok"
        assert block["likely_grade"] == 10
        assert block["probabilities"]
        assert block["confidence"] == "high"
        assert block["model_version"]
        assert len(block["by_company"]) == 3
        assert block["physical"]["likely_grade"] == 10

    def test_it_reports_not_assessed_before_any_assessment(self, client: TestClient, card: dict):
        block = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]
        assert block["status"] == "not_assessed"
        assert block["likely_grade"] is None

    def test_it_recomputes_after_a_reassessment(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        before = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]

        self._assess(client, card["id"], creases="severe")
        after = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]

        assert after["likely_grade"] < before["likely_grade"]
        assert after["max_grade_cap"] == 3.0

    def test_the_why_panel_mentions_the_grade(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        body = client.get(f"/api/cards/{card['id']}/evaluation").json()
        assert any("Likely" in item["text"] for item in body["explanation"])

    def test_predicting_no_longer_blocks_a_recommendation(self, client: TestClient, card: dict):
        self._assess(client, card["id"])
        blockers = client.get(f"/api/cards/{card['id']}/evaluation").json()["blockers"]
        assert not any("probability model" in blocker for blocker in blockers)
        # Market data is still missing, and still says so.
        assert any("sale" in blocker.lower() for blocker in blockers)


class TestSettings:
    def test_model_parameters_are_configurable(self, client: TestClient, card: dict):
        TestApi()._assess(client, card["id"], scratches="minor")
        before = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]

        # Turn off the worst-attribute weighting: the estimate becomes a plain average.
        client.patch("/api/settings", json={"values": {"grade_model_worst_weight": 0.0}})
        after = client.get(f"/api/cards/{card['id']}/evaluation").json()["grade_prediction"]

        assert after["base_grade"] > before["base_grade"]

    def test_weights_are_validated(self, client: TestClient):
        response = client.patch(
            "/api/settings", json={"values": {"grade_model_weights": {"centering": 1.0}}}
        )
        assert response.status_code == 400

    def test_defaults_are_exposed_with_their_descriptions(self, client: TestClient, db):
        definitions = {
            item["key"]: item for item in client.get("/api/settings").json()["definitions"]
        }
        assert definitions["grade_model_worst_weight"]["category"] == "grade_model"
        assert definitions["grade_model_base_sigma"]["description"]
        assert settings_service.get(db, "grade_model_base_sigma") == 0.45
