"""Configuration: settings, grading companies/tiers, selling profiles, seed data.

The load-bearing claim under test is spec section 10: nothing about a grading
company is hard-coded, so changing a price is a row edit that the engine picks
up immediately.
"""

from __future__ import annotations

import os

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


def test_the_last_stub_is_the_one_needing_a_model_not_an_engine(client: TestClient):
    """Image identification is all that is left, and it needs a vision provider.

    It keeps reporting the phase that owns it rather than being quietly deleted,
    and it still promises the thing that matters: a suggestion you confirm.
    """
    response = client.post("/api/cards/identify")
    assert response.status_code == 501
    body = response.json()["error"]
    assert body["details"]["phase"] == 3
    assert "never applied" in body["message"]


def test_submissions_are_no_longer_a_later_phase(client: TestClient):
    """Phase 6 landed, so the stub has to be gone rather than merely bypassed."""
    response = client.get("/api/submissions")
    assert response.status_code == 200
    assert response.json() == []


def test_accuracy_is_no_longer_a_later_phase(client: TestClient):
    """Phase 8 landed. The stub has to be gone, not merely shadowed by a real route."""
    response = client.get("/api/analytics/accuracy")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data", "no results yet, honestly reported"


def test_analytics_is_no_longer_a_later_phase(client: TestClient):
    """Phase 7 landed. The stub has to be gone, not merely shadowed by a real route."""
    response = client.get("/api/analytics/opportunities")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data", "empty collection, honestly reported"


def test_refresh_refuses_when_every_source_is_off(client: TestClient):
    """Turning the last one off must leave a clear message, not a silent no-op."""
    for code in ("pokemontcg_io",):
        client.patch(f"/api/data-sources/{code}", json={"enabled": False})

    response = client.post("/api/market/refresh")
    assert response.status_code == 409
    message = response.json()["error"]["message"]
    assert "No market-data source is enabled" in message


def test_enabling_a_source_without_an_adapter_is_refused(client: TestClient):
    """The disabled rows advertise what is planned, not what can be switched on."""
    response = client.patch("/api/data-sources/cardmarket", json={"enabled": True})
    assert response.status_code == 409
    assert "has no adapter" in response.json()["error"]["message"]


def test_ebay_ships_off_but_ready(client: TestClient):
    """The opposite call from the catalogue source, for the opposite reason.

    It cannot do anything without two credentials, so switching it on by default
    would produce a source that is on and failing rather than one that works.
    """
    row = {r["code"]: r for r in client.get("/api/data-sources").json()}["ebay"]
    assert row["has_adapter"] is True
    assert row["enabled"] is False
    assert [c["env_var"] for c in row["credentials"]] == [
        "SLABSTACK_EBAY_APP_ID",
        "SLABSTACK_EBAY_CERT_ID",
    ], "both halves of the credential pair are reported, not just the first"


def test_the_free_catalogue_source_ships_switched_on(client: TestClient):
    """No signup, no approval, no key — so nothing is gained by making them find a switch."""
    sources = {row["code"]: row for row in client.get("/api/data-sources").json()}
    row = sources["pokemontcg_io"]
    assert row["has_adapter"] is True
    assert row["enabled"] is True, "the one source that works with no setup is on"

    off = client.patch("/api/data-sources/pokemontcg_io", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False, "and one click turns it off"


def test_sources_needing_a_key_stay_off(client: TestClient):
    """A source that cannot work without setup must not claim to be running."""
    sources = {row["code"]: row for row in client.get("/api/data-sources").json()}
    for code in ("pokeprice", "pricecharting", "ebay", "cardmarket", "tcgplayer"):
        assert sources[code]["enabled"] is False, code


def test_a_key_in_dotenv_actually_reaches_the_provider_lookup(tmp_path, monkeypatch):
    """`.env` has to populate os.environ, not just the Settings model.

    Provider credentials cannot be declared fields on Settings — which variable
    a source reads is a row in `data_sources`, chosen at runtime — so they are
    looked up with os.environ.get. Pydantic reads `.env` into the model and
    discards anything undeclared, which meant a key written into `.env` was
    silently dropped and reported as "not set" while the user looked at the line
    they had just added.
    """
    from app import config

    env_file = tmp_path / ".env"
    env_file.write_text("SLABSTACK_TEST_PROVIDER_KEY=written-in-dotenv\n")
    monkeypatch.setattr(config, "ENV_FILES", (env_file,))
    monkeypatch.delenv("SLABSTACK_TEST_PROVIDER_KEY", raising=False)

    assert config.load_env_files() == ["SLABSTACK_TEST_PROVIDER_KEY"]
    assert os.environ["SLABSTACK_TEST_PROVIDER_KEY"] == "written-in-dotenv"


def test_an_exported_variable_beats_dotenv(tmp_path, monkeypatch):
    """The shell is the more deliberate statement; a stale file must not win."""
    from app import config

    env_file = tmp_path / ".env"
    env_file.write_text("SLABSTACK_TEST_PROVIDER_KEY=stale-file-value\n")
    monkeypatch.setattr(config, "ENV_FILES", (env_file,))
    monkeypatch.setenv("SLABSTACK_TEST_PROVIDER_KEY", "set-on-the-command-line")

    assert config.load_env_files() == []
    assert os.environ["SLABSTACK_TEST_PROVIDER_KEY"] == "set-on-the-command-line"
