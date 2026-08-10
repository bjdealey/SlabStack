"""Card CRUD, search and the per-copy split rule."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_card_resolves_set_and_builds_catalog_key(card: dict):
    # "EVS" was typed as free text; the seeded set catalogue filled in the name.
    assert card["set_name"] == "Evolving Skies"
    assert card["set_id"]
    assert card["catalog_key"] == "english|evs|215-203|alternate-art|unlimited"
    assert card["purchase_price"] == 185.0


def test_catalog_key_separates_markets(client: TestClient):
    """A Japanese copy and a reverse holo are different markets, not the same card."""
    base = {"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"}
    english = client.post("/api/cards", json={**base, "variant": "Alternate Art"}).json()
    japanese = client.post(
        "/api/cards", json={**base, "variant": "Alternate Art", "language": "Japanese"}
    ).json()
    reverse = client.post("/api/cards", json={**base, "variant": "Reverse Holo"}).json()

    keys = {english["catalog_key"], japanese["catalog_key"], reverse["catalog_key"]}
    assert len(keys) == 3


def test_duplicate_copies_share_a_catalog_key(client: TestClient):
    payload = {"name": "Charizard ex", "set_code": "MEW", "card_number": "199/165"}
    first = client.post("/api/cards", json=payload).json()
    second = client.post("/api/cards", json=payload).json()
    assert first["id"] != second["id"]
    assert first["catalog_key"] == second["catalog_key"]


def test_get_update_delete(client: TestClient, card: dict):
    card_id = card["id"]

    assert client.get(f"/api/cards/{card_id}").status_code == 200

    updated = client.patch(
        f"/api/cards/{card_id}", json={"user_raw_value": 210.5, "notes": "Sharp corners"}
    )
    assert updated.status_code == 200
    assert updated.json()["user_raw_value"] == 210.5
    assert updated.json()["notes"] == "Sharp corners"

    assert client.delete(f"/api/cards/{card_id}").status_code == 204
    assert client.get(f"/api/cards/{card_id}").status_code == 404


def test_patch_leaves_absent_fields_alone(client: TestClient, card: dict):
    client.patch(f"/api/cards/{card['id']}", json={"notes": "kept"})
    result = client.patch(f"/api/cards/{card['id']}", json={"rarity": "Secret Rare"}).json()
    assert result["notes"] == "kept"
    assert result["rarity"] == "Secret Rare"


def test_missing_card_returns_error_envelope(client: TestClient):
    response = client.get("/api/cards/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "does-not-exist" in body["error"]["message"]


def test_validation_error_envelope(client: TestClient):
    response = client.post("/api/cards", json={"name": "X", "language": "Klingon"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "language" in response.json()["error"]["details"]["fields"]


class TestSearch:
    def _seed(self, client: TestClient) -> None:
        for payload in (
            {"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"},
            {"name": "Charizard ex", "set_code": "MEW", "card_number": "199/165"},
            {"name": "Gengar VMAX", "set_code": "FST", "card_number": "157/264", "is_promo": True},
        ):
            client.post("/api/cards", json=payload)

    def test_free_text_matches_name_and_number(self, client: TestClient):
        self._seed(client)
        assert client.get("/api/cards", params={"q": "umbreon"}).json()["total"] == 1
        assert client.get("/api/cards", params={"q": "199/165"}).json()["total"] == 1
        assert client.get("/api/cards", params={"q": "vmax"}).json()["total"] == 2

    def test_search_is_case_insensitive(self, client: TestClient):
        self._seed(client)
        assert client.get("/api/cards", params={"q": "CHARIZARD"}).json()["total"] == 1

    def test_filters(self, client: TestClient):
        self._seed(client)
        assert client.get("/api/cards", params={"set_code": "evs"}).json()["total"] == 1
        assert client.get("/api/cards", params={"is_promo": True}).json()["total"] == 1
        assert client.get("/api/cards", params={"has_images": False}).json()["total"] == 3

    def test_pagination_envelope(self, client: TestClient):
        self._seed(client)
        page = client.get("/api/cards", params={"page": 1, "page_size": 2}).json()
        assert page["total"] == 3
        assert page["total_pages"] == 2
        assert len(page["items"]) == 2

    def test_sorting(self, client: TestClient):
        self._seed(client)
        names = [
            item["name"]
            for item in client.get("/api/cards", params={"sort": "name", "order": "asc"}).json()[
                "items"
            ]
        ]
        assert names == sorted(names)


class TestSplit:
    def test_split_creates_one_row_per_physical_card(self, client: TestClient):
        card = client.post(
            "/api/cards", json={"name": "Pikachu", "set_code": "MEW", "quantity": 3}
        ).json()

        result = client.post(f"/api/cards/{card['id']}/split").json()
        assert len(result) == 3
        assert all(item["quantity"] == 1 for item in result)
        assert client.get("/api/cards").json()["total"] == 3

    def test_partial_split(self, client: TestClient):
        card = client.post(
            "/api/cards", json={"name": "Pikachu", "set_code": "MEW", "quantity": 5}
        ).json()
        result = client.post(f"/api/cards/{card['id']}/split", json={"count": 2}).json()
        quantities = sorted(item["quantity"] for item in result)
        assert quantities == [1, 4]

    def test_single_copy_cannot_split(self, client: TestClient, card: dict):
        response = client.post(f"/api/cards/{card['id']}/split")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "cannot_split"


def test_bulk_create(client: TestClient):
    response = client.post(
        "/api/cards/bulk",
        json={"cards": [{"name": f"Card {index}", "set_code": "MEW"} for index in range(25)]},
    )
    assert response.status_code == 201
    assert len(response.json()) == 25
    assert client.get("/api/cards").json()["total"] == 25
