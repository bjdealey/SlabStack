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
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test tries to make a real HTTP request.

    Added after one did. Enabling a data source by default meant a test that
    previously exercised the "source is off" path started loading the real
    adapter instead — and it quietly made an outbound request to a live API,
    passing or failing depending on somebody else's uptime.

    Adapters take an injectable transport precisely so they can be tested
    without a network. This makes forgetting to pass one an error rather than a
    surprise, and names the fix in the message.
    """

    def refuse(self, url: str, **_: object) -> None:
        raise AssertionError(
            f"A test tried to reach {url} for real. Pass a RecordedTransport to the provider, "
            "or monkeypatch load_provider — see tests/test_market_providers.py."
        )

    # Every method that can reach the network, not just the first one written.
    # ``post_form`` arrived with OAuth and would otherwise have been an
    # unguarded hole in exactly the guard that exists to catch this.
    for method in ("get_json", "post_form"):
        monkeypatch.setattr(
            f"app.services.market_data.http.HttpxTransport.{method}", refuse, raising=True
        )


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
def seeded_db() -> Iterator[SessionLocal]:
    """A session with reference data present.

    ``db`` alone is empty: seeding normally happens at app startup, which only
    the ``client`` fixture triggers. Tests that exercise services directly —
    the grade model against real grading companies and rules — need the rows
    without going through HTTP.
    """
    from app.services import seed

    session = SessionLocal()
    try:
        seed.seed_all(session)
        session.commit()
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
