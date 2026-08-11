"""Configuration: settings, grading companies/tiers, selling profiles, seed data.

The load-bearing claim under test is spec section 10: nothing about a grading
company is hard-coded, so changing a price is a row edit that the engine picks
up immediately.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestSettings:
    def test_defaults_are_served_with_their_definitions(self, client: TestClient):
        body = client.get("/api/settings").json()
        assert body["values"]["currency"] == "GBP"
        assert body["values"]["risk_tolerance"] == "balanced"
        assert body["values"]["decision_score_weights"]["profitability"] == 35

        keys = {definition["key"] for definition in body["definitions"]}
        assert keys == set(body["values"])

    def test_update_and_reset(self, client: TestClient):
        updated = client.patch("/api/settings", json={"values": {"minimum_roi_pct": 45.0}})
        assert updated.status_code == 200
        assert updated.json()["values"]["minimum_roi_pct"] == 45.0

        reset = client.post("/api/settings/minimum_roi_pct/reset").json()
        assert reset["values"]["minimum_roi_pct"] == 25.0

    def test_unknown_setting_rejected(self, client: TestClient):
        response = client.patch("/api/settings", json={"values": {"make_me_rich": True}})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unknown_setting"

    def test_out_of_range_rejected(self, client: TestClient):
        response = client.patch("/api/settings", json={"values": {"minimum_roi_pct": -5}})
        assert response.status_code == 400

    def test_decision_weights_must_total_one_hundred(self, client: TestClient):
        response = client.patch(
            "/api/settings",
            json={
                "values": {
                    "decision_score_weights": {
                        "profitability": 50, "grade_probability": 25,
                        "liquidity": 20, "trend": 10, "risk": 10,
                    }
                }
            },
        )
        assert response.status_code == 400
        assert "100" in response.json()["error"]["message"]


class TestGradingConfiguration:
    def test_seeded_companies(self, client: TestClient):
        companies = {c["code"]: c for c in client.get("/api/grading/companies").json()}
        assert {"PSA", "CGC", "ACE", "BGS", "SGC"} <= set(companies)
        assert companies["PSA"]["market_recognition_score"] > companies["ACE"]["market_recognition_score"]

    def test_cgc_tiers_carry_their_minimums_and_value_ceilings(self, client: TestClient):
        cgc = next(c for c in client.get("/api/grading/companies").json() if c["code"] == "CGC")
        tiers = {tier["tier_name"]: tier for tier in cgc["tiers"]}

        assert tiers["Bulk"]["price"] == 16.80
        assert tiers["Bulk"]["minimum_cards"] == 25
        assert tiers["Bulk"]["max_declared_value"] == 400.0
        assert tiers["Economy"]["price"] == 19.00
        assert tiers["Standard"]["max_declared_value"] == 2500.0

    def test_unpriced_tiers_are_inactive_rather_than_free(self, client: TestClient):
        psa = next(c for c in client.get("/api/grading/companies").json() if c["code"] == "PSA")
        for tier in psa["tiers"]:
            assert tier["price"] == 0.0
            assert tier["active"] is False
            assert tier["notes"]

    def test_price_change_is_a_row_edit(self, client: TestClient, card: dict):
        cgc = next(c for c in client.get("/api/grading/companies").json() if c["code"] == "CGC")
        bulk = next(tier for tier in cgc["tiers"] if tier["tier_name"] == "Bulk")

        client.patch(
            f"/api/grading/tiers/{bulk['id']}",
            json={
                "tier_code": bulk["tier_code"],
                "tier_name": bulk["tier_name"],
                "price": 18.50,
                "minimum_cards": 30,
            },
        )
        options = client.get(f"/api/cards/{card['id']}/evaluation").json()["grading_options"]["options"]
        updated = next(o for o in options if o["company_code"] == "CGC" and o["tier_name"] == "Bulk")
        assert updated["grading_fee"] == 18.50
        assert updated["minimum_cards"] == 30

    def test_activating_a_psa_tier_makes_it_an_option(self, client: TestClient, card: dict):
        psa = next(c for c in client.get("/api/grading/companies").json() if c["code"] == "PSA")
        tier = psa["tiers"][0]
        client.patch(
            f"/api/grading/tiers/{tier['id']}",
            json={
                "tier_code": tier["tier_code"],
                "tier_name": tier["tier_name"],
                "price": 22.00,
                "active": True,
            },
        )
        # PSA Bulk takes 20 cards, so cost it as part of a batch that size.
        options = client.get(
            f"/api/cards/{card['id']}/evaluation", params={"batch_size": 20}
        ).json()["grading_options"]["options"]
        psa_options = [o for o in options if o["company_code"] == "PSA" and o["available"]]
        assert psa_options and psa_options[0]["grading_fee"] == 22.00

    def test_add_a_new_grading_company(self, client: TestClient):
        created = client.post(
            "/api/grading/companies",
            json={"code": "TAG", "name": "TAG Grading", "market_recognition_score": 4.0},
        )
        assert created.status_code == 201

        tier = client.post(
            f"/api/grading/companies/{created.json()['id']}/tiers",
            json={"tier_code": "std", "tier_name": "Standard", "price": 30.0},
        )
        assert tier.status_code == 201
        assert tier.json()["price"] == 30.0

    def test_duplicate_company_code_conflicts(self, client: TestClient):
        response = client.post("/api/grading/companies", json={"code": "PSA", "name": "Copy"})
        assert response.status_code == 409


class TestSellingProfiles:
    def test_seeded_profiles(self, client: TestClient):
        profiles = {p["code"]: p for p in client.get("/api/selling-profiles").json()}
        assert profiles["ebay_uk"]["is_default"] is True
        assert profiles["private"]["platform_fee_pct"] == 0.0
        assert profiles["ebay_uk"]["payment_fixed_fee"] == 0.30

    def test_only_one_default(self, client: TestClient):
        profiles = client.get("/api/selling-profiles").json()
        cardmarket = next(p for p in profiles if p["code"] == "cardmarket")
        client.patch(
            f"/api/selling-profiles/{cardmarket['id']}",
            json={"code": "cardmarket", "name": "Cardmarket", "is_default": True},
        )
        defaults = [p for p in client.get("/api/selling-profiles").json() if p["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["code"] == "cardmarket"


class TestDataSources:
    def test_network_sources_start_disabled(self, client: TestClient):
        sources = {s["code"]: s for s in client.get("/api/data-sources").json()}
        assert sources["manual"]["enabled"] is True
        assert sources["pokeprice"]["enabled"] is False
        assert sources["ebay"]["enabled"] is False

    def test_api_keys_are_never_returned(self, client: TestClient):
        for source in client.get("/api/data-sources").json():
            assert "api_key" not in source
            assert isinstance(source["api_key_present"], bool)


class TestSeeding:
    def test_seed_is_idempotent(self, client: TestClient):
        first = client.post("/api/system/seed").json()["counts"]
        second = client.post("/api/system/seed").json()["counts"]
        assert sum(first.values()) == 0  # startup already seeded
        assert sum(second.values()) == 0

    def test_seed_does_not_overwrite_user_edits(self, client: TestClient):
        cgc = next(c for c in client.get("/api/grading/companies").json() if c["code"] == "CGC")
        bulk = next(tier for tier in cgc["tiers"] if tier["tier_name"] == "Bulk")
        client.patch(
            f"/api/grading/tiers/{bulk['id']}",
            json={"tier_code": "bulk", "tier_name": "Bulk", "price": 21.00},
        )

        client.post("/api/system/seed")

        cgc = next(c for c in client.get("/api/grading/companies").json() if c["code"] == "CGC")
        assert next(t for t in cgc["tiers"] if t["tier_name"] == "Bulk")["price"] == 21.00


def test_enums_endpoint_feeds_the_ui(client: TestClient):
    body = client.get("/api/meta/enums").json()
    assert "severity" in body["enums"]
    assert body["enums"]["severity"] == ["none", "minor", "moderate", "severe", "unknown"]
    assert len(body["defect_fields"]) == 16
    assert "decision" in body["enums"]


def test_later_phase_endpoints_report_their_phase(client: TestClient):
    for path, phase in (("/api/analytics/accuracy", 8),):
        response = client.get(path)
        assert response.status_code == 501, path
        assert response.json()["error"]["details"]["phase"] == phase


def test_submissions_are_no_longer_a_later_phase(client: TestClient):
    """Phase 6 landed, so the stub has to be gone rather than merely bypassed."""
    response = client.get("/api/submissions")
    assert response.status_code == 200
    assert response.json() == []


def test_analytics_is_no_longer_a_later_phase(client: TestClient):
    """Phase 7 landed. The stub has to be gone, not merely shadowed by a real route."""
    response = client.get("/api/analytics/opportunities")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data", "empty collection, honestly reported"


def test_provider_sync_says_valuation_already_works_without_it(client: TestClient):
    """A 501 should tell the user what they *can* do, not just what they cannot."""
    response = client.post("/api/market/refresh")
    assert response.status_code == 501
    body = response.json()["error"]
    assert body["details"]["phase"] == 3
    assert "CSV" in body["message"]
