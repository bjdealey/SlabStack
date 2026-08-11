"""Collection summary, groups and catalogue reference data."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _add(client: TestClient, **overrides) -> dict:
    payload = {"name": "Card", "set_code": "MEW", **overrides}
    return client.post("/api/cards", json=payload).json()


class TestSummary:
    def test_empty_collection(self, client: TestClient):
        summary = client.get("/api/collection/summary").json()
        assert summary["totals"]["cards"] == 0
        assert summary["values"]["known_raw_value"] == 0.0

    def test_totals_count_copies_separately_from_rows(self, client: TestClient):
        _add(client, name="Pikachu", quantity=4)
        _add(client, name="Charizard")
        summary = client.get("/api/collection/summary").json()
        assert summary["totals"]["cards"] == 2
        assert summary["totals"]["copies"] == 5

    def test_value_uses_the_users_number_over_purchase_price(self, client: TestClient):
        _add(client, name="A", purchase_price=100.0)
        _add(client, name="B", purchase_price=50.0, user_raw_value=90.0)
        values = client.get("/api/collection/summary").json()["values"]

        assert values["purchase_total"] == 150.0
        assert values["known_raw_value"] == 190.0  # 100 + 90, not 100 + 50
        assert values["cards_with_value"] == 2

    def test_uncalculated_figures_are_null_not_zero(self, client: TestClient):
        _add(client, name="A", purchase_price=100.0)
        values = client.get("/api/collection/summary").json()["values"]
        # A zero here would read as "no upside" rather than "not calculated yet".
        assert values["potential_graded_value"] is None
        assert values["expected_profit"] is None
        assert values["values_reason"]

    def test_readiness_shows_what_is_blocking_analysis(self, client: TestClient, sample_image: bytes):
        card = _add(client, name="A")
        client.post(
            f"/api/cards/{card['id']}/images",
            files={"files": ("f.jpg", sample_image, "image/jpeg")},
            data={"side": "front"},
        )
        client.put(f"/api/cards/{card['id']}/condition", json={"front": {"corner_tl": "none"}})

        readiness = {item["key"]: item for item in client.get("/api/collection/summary").json()["readiness"]}
        assert readiness["photographed"]["count"] == 1
        assert readiness["assessed"]["count"] == 1
        assert readiness["market_data"]["count"] == 0
        assert readiness["market_data"]["action"]

    def test_decision_counts_reflect_user_overrides(self, client: TestClient):
        first = _add(client, name="A")
        _add(client, name="B")
        client.patch(f"/api/cards/{first['id']}", json={"decision_override": "hold"})

        decisions = client.get("/api/collection/summary").json()["decisions"]
        assert decisions["hold"] == 1
        assert decisions["insufficient_data"] == 1

    def test_review_due_counts_holds_that_have_come_around(self, client: TestClient):
        card = _add(client, name="A")
        client.patch(f"/api/cards/{card['id']}", json={"review_after": "2020-01-01"})
        assert client.get("/api/collection/summary").json()["review_due"] == 1

    def test_by_set_breakdown(self, client: TestClient):
        _add(client, name="A", set_code="EVS")
        _add(client, name="B", set_code="EVS")
        _add(client, name="C", set_code="MEW")
        by_set = client.get("/api/collection/summary").json()["by_set"]
        assert by_set[0]["cards"] == 2


class TestFacets:
    def test_facets_only_list_what_is_present(self, client: TestClient):
        _add(client, name="A", set_code="EVS", variant="Alternate Art", rarity="Secret Rare")
        facets = client.get("/api/collection/facets").json()
        assert facets["sets"] == ["EVS"]
        assert facets["variants"] == ["Alternate Art"]
        assert facets["rarities"] == ["Secret Rare"]


class TestGroups:
    def test_create_add_and_filter(self, client: TestClient):
        group = client.post("/api/groups", json={"name": "To grade"}).json()
        first = _add(client, name="A")
        _add(client, name="B")

        client.post(f"/api/groups/{group['id']}/cards", json={"card_ids": [first["id"]]})

        groups = {item["name"]: item for item in client.get("/api/groups").json()}
        assert groups["To grade"]["card_count"] == 1
        filtered = client.get("/api/cards", params={"group_id": group["id"]}).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["id"] == first["id"]

    def test_adding_twice_is_harmless(self, client: TestClient):
        group = client.post("/api/groups", json={"name": "Dupes"}).json()
        card = _add(client, name="A")
        for _ in range(2):
            client.post(f"/api/groups/{group['id']}/cards", json={"card_ids": [card["id"]]})
        assert client.get("/api/cards", params={"group_id": group["id"]}).json()["total"] == 1

    def test_remove_card(self, client: TestClient):
        group = client.post("/api/groups", json={"name": "X"}).json()
        card = _add(client, name="A")
        client.post(f"/api/groups/{group['id']}/cards", json={"card_ids": [card["id"]]})
        assert client.delete(f"/api/groups/{group['id']}/cards/{card['id']}").status_code == 204
        assert client.get("/api/cards", params={"group_id": group["id"]}).json()["total"] == 0

    def test_duplicate_group_name_conflicts(self, client: TestClient):
        client.post("/api/groups", json={"name": "Same"})
        assert client.post("/api/groups", json={"name": "Same"}).status_code == 409

    def test_deleting_a_group_keeps_the_cards(self, client: TestClient):
        group = client.post("/api/groups", json={"name": "Temp"}).json()
        card = _add(client, name="A")
        client.post(f"/api/groups/{group['id']}/cards", json={"card_ids": [card["id"]]})
        client.delete(f"/api/groups/{group['id']}")
        assert client.get(f"/api/cards/{card['id']}").status_code == 200


class TestCatalog:
    def test_starter_sets_are_seeded_and_searchable(self, client: TestClient):
        assert len(client.get("/api/sets").json()) >= 10
        results = client.get("/api/sets", params={"q": "evolving"}).json()
        assert results[0]["code"] == "EVS"

    def test_variants_are_seeded(self, client: TestClient):
        codes = {variant["code"] for variant in client.get("/api/variants").json()}
        assert {"alt-art", "reverse-holo", "illustration-rare"} <= codes

    def test_user_can_add_a_set_and_a_variant(self, client: TestClient):
        assert client.post(
            "/api/sets", json={"code": "XYZ", "name": "Custom Set"}
        ).status_code == 201
        assert client.post(
            "/api/variants", json={"code": "stamped", "name": "Staff Stamped"}
        ).status_code == 201

    def test_set_in_use_cannot_be_deleted(self, client: TestClient):
        card = _add(client, name="A", set_code="EVS")
        assert card["set_id"]
        response = client.delete(f"/api/sets/{card['set_id']}")
        assert response.status_code == 409

    def test_builtin_variant_cannot_be_deleted(self, client: TestClient):
        variant = client.get("/api/variants").json()[0]
        assert client.delete(f"/api/variants/{variant['id']}").status_code == 409
