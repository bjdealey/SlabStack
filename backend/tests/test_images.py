"""Image upload, validation and lifecycle."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image


def _upload(client: TestClient, card_id: str, content: bytes, side: str = "front"):
    return client.post(
        f"/api/cards/{card_id}/images",
        files={"files": (f"{side}.jpg", content, "image/jpeg")},
        data={"side": side},
    )


def test_upload_stores_metadata_and_thumbnail(client: TestClient, card: dict, sample_image: bytes):
    response = _upload(client, card["id"], sample_image)
    assert response.status_code == 201

    image = response.json()[0]
    assert image["side"] == "front"
    assert image["width"] == 734 and image["height"] == 1024
    assert image["is_primary"] is True
    assert image["thumbnail_url"]

    assert client.get(image["url"]).status_code == 200
    thumbnail = client.get(image["thumbnail_url"])
    assert thumbnail.status_code == 200
    with Image.open(io.BytesIO(thumbnail.content)) as thumb:
        assert max(thumb.size) <= 480


def test_front_and_back_each_get_their_own_primary(client: TestClient, card: dict, sample_image: bytes):
    _upload(client, card["id"], sample_image, "front")
    _upload(client, card["id"], sample_image, "back")
    images = client.get(f"/api/cards/{card['id']}/images").json()
    primaries = {image["side"] for image in images if image["is_primary"]}
    assert primaries == {"front", "back"}


def test_second_upload_is_not_primary_until_promoted(client: TestClient, card: dict, sample_image: bytes):
    first = _upload(client, card["id"], sample_image).json()[0]
    second = _upload(client, card["id"], sample_image).json()[0]
    assert second["is_primary"] is False

    promoted = client.patch(f"/api/images/{second['id']}", json={"is_primary": True}).json()
    assert promoted["is_primary"] is True

    images = {image["id"]: image for image in client.get(f"/api/cards/{card['id']}/images").json()}
    assert images[first["id"]]["is_primary"] is False


def test_deleting_the_primary_promotes_a_replacement(client: TestClient, card: dict, sample_image: bytes):
    first = _upload(client, card["id"], sample_image).json()[0]
    second = _upload(client, card["id"], sample_image).json()[0]

    assert client.delete(f"/api/images/{first['id']}").status_code == 204
    remaining = client.get(f"/api/cards/{card['id']}/images").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == second["id"]
    assert remaining[0]["is_primary"] is True


def test_non_image_upload_is_rejected(client: TestClient, card: dict):
    response = client.post(
        f"/api/cards/{card['id']}/images",
        files={"files": ("payload.jpg", b"this is not an image", "image/jpeg")},
        data={"side": "front"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_image"


def test_content_is_validated_not_the_declared_type(client: TestClient, card: dict):
    """A .jpg extension and image/jpeg header do not make a file an image."""
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # PNG magic, truncated body
    response = client.post(
        f"/api/cards/{card['id']}/images",
        files={"files": ("real.jpg", fake, "image/jpeg")},
        data={"side": "front"},
    )
    assert response.status_code == 400


def test_deleting_a_card_removes_its_image_files(client: TestClient, card: dict, sample_image: bytes, data_dir):
    image = _upload(client, card["id"], sample_image).json()[0]
    stored = list((data_dir / "media" / "cards" / card["id"]).glob("*"))
    assert stored

    assert client.delete(f"/api/cards/{card['id']}").status_code == 204
    assert client.get(image["url"]).status_code == 404
    assert not list((data_dir / "media" / "cards" / card["id"]).glob("*"))


def test_card_exposes_a_primary_image_url(client: TestClient, card: dict, sample_image: bytes):
    _upload(client, card["id"], sample_image)
    fetched = client.get(f"/api/cards/{card['id']}").json()
    assert fetched["primary_image_url"]
    assert len(fetched["images"]) == 1
