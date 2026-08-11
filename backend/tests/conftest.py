"""Test fixtures.

Each test module gets a throwaway data directory, so tests never touch the
developer's real collection and can run in parallel.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must be set before app.config is imported anywhere.
_TEMP_DIR = tempfile.mkdtemp(prefix="slabstack-tests-")
os.environ["SLABSTACK_DATA_DIR"] = _TEMP_DIR

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(_TEMP_DIR)


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db() -> Iterator[SessionLocal]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (734, 1024), color=(30, 90, 160)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def card(client: TestClient) -> dict:
    response = client.post(
        "/api/cards",
        json={
            "name": "Umbreon VMAX",
            "set_code": "EVS",
            "card_number": "215/203",
            "variant": "Alternate Art",
            "language": "English",
            "purchase_price": 185.0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
