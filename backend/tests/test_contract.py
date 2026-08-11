"""Guards on the published API contract.

docs/API_CONTRACT.md is what the React client is written against. These tests
fail if a path disappears, if the evaluation envelope loses a block, or if a
money field silently changes shape — the three ways a client breaks quietly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

IMPLEMENTED_PATHS = [
    ("get", "/api/health"),
    ("post", "/api/system/seed"),
    ("get", "/api/meta/enums"),
    ("get", "/api/cards"),
    ("post", "/api/cards"),
    ("post", "/api/cards/bulk"),
    ("get", "/api/cards/{card_id}"),
    ("patch", "/api/cards/{card_id}"),
    ("delete", "/api/cards/{card_id}"),
    ("post", "/api/cards/{card_id}/split"),
    ("get", "/api/cards/{card_id}/evaluation"),
    ("post", "/api/cards/{card_id}/images"),
    ("get", "/api/images/{image_id}/file"),
    ("get", "/api/images/{image_id}/thumbnail"),
    ("patch", "/api/images/{image_id}"),
    ("delete", "/api/images/{image_id}"),
    ("get", "/api/cards/{card_id}/condition"),
    ("put", "/api/cards/{card_id}/condition"),
    ("get", "/api/cards/{card_id}/condition/history"),
    ("get", "/api/collection/summary"),
    ("get", "/api/collection/facets"),
    ("get", "/api/groups"),
    ("post", "/api/groups"),
    ("get", "/api/sets"),
    ("get", "/api/variants"),
    ("get", "/api/grading/companies"),
    ("post", "/api/grading/companies/{company_id}/tiers"),
    ("patch", "/api/grading/tiers/{tier_id}"),
    ("get", "/api/selling-profiles"),
    ("get", "/api/data-sources"),
    ("get", "/api/settings"),
    ("patch", "/api/settings"),
]

LATER_PHASE_PATHS = [
    ("post", "/api/cards/identify"),
    # Valuation is built; fetching sales from a provider still needs credentials.
    ("post", "/api/market/refresh"),
    ("get", "/api/submissions"),
    ("post", "/api/submissions/optimise"),
    ("get", "/api/analytics/opportunities"),
    ("get", "/api/analytics/accuracy"),
]

# Paths that graduated from a 501 stub to a working endpoint. Listed so that
# removing one by accident fails a test rather than silently 404ing a client.
DELIVERED_PATHS = [
    ("get", "/api/market/prices"),
    ("get", "/api/market/sales"),
    ("get", "/api/cards/{card_id}/market"),
    ("post", "/api/cards/{card_id}/market/sales"),
    ("post", "/api/cards/{card_id}/market/sales/import"),
    ("post", "/api/cards/{card_id}/market/recompute"),
    ("get", "/api/cards/{card_id}/market/history"),
    ("put", "/api/market/sales/{sale_id}/exclusion"),
]

EVALUATION_BLOCKS = [
    "raw",
    "condition",
    "grade_prediction",
    "market",
    "liquidity",
    "trend",
    "grading_options",
    "expected_outcomes",
    "recommendation",
]


@pytest.fixture(scope="module")
def openapi() -> dict:
    from app.main import app

    with TestClient(app) as client:
        return client.get("/api/openapi.json").json()


@pytest.mark.parametrize(("method", "path"), IMPLEMENTED_PATHS)
def test_documented_path_exists(openapi: dict, method: str, path: str):
    assert path in openapi["paths"], f"{path} is documented but not registered"
    assert method in openapi["paths"][path], f"{method.upper()} {path} is missing"


@pytest.mark.parametrize(("method", "path"), LATER_PHASE_PATHS)
def test_later_phase_path_is_registered(openapi: dict, method: str, path: str):
    """Stubs stay registered so the contract is executable, not aspirational."""
    assert path in openapi["paths"], f"{path} should be registered as a 501 stub"
    assert method in openapi["paths"][path]


@pytest.mark.parametrize(("method", "path"), DELIVERED_PATHS)
def test_delivered_path_is_registered(openapi: dict, method: str, path: str):
    assert path in openapi["paths"], f"{path} should be a working endpoint"
    assert method in openapi["paths"][path]


def test_evaluation_envelope_keeps_every_block(openapi: dict):
    schema = openapi["components"]["schemas"]["CardEvaluation"]
    for block in EVALUATION_BLOCKS:
        assert block in schema["properties"], f"evaluation lost the '{block}' block"
    for field in ("card_id", "evaluated_at", "engine_version", "currency", "explanation", "blockers"):
        assert field in schema["properties"]


def test_no_minor_unit_field_leaks_into_the_api(openapi: dict):
    """Money is exposed in major units; `*_minor` columns must never surface."""
    leaked = [
        f"{name}.{field}"
        for name, schema in openapi["components"]["schemas"].items()
        for field in schema.get("properties", {})
        if field.endswith("_minor")
    ]
    assert leaked == [], f"minor-unit fields leaked into the API: {leaked}"


def test_error_envelope_is_consistent(client: TestClient):
    responses = [
        client.get("/api/cards/nope"),
        client.post("/api/cards", json={"name": ""}),
        client.get("/api/market/prices"),
    ]
    for response in responses:
        body = response.json()
        assert set(body) == {"error"}
        assert {"code", "message"} <= set(body["error"])


def test_pagination_envelope_is_consistent(client: TestClient):
    body = client.get("/api/cards").json()
    assert set(body) == {"items", "total", "page", "page_size", "total_pages"}


def test_money_is_serialised_in_major_units(client: TestClient):
    card = client.post("/api/cards", json={"name": "X", "purchase_price": 18.8}).json()
    assert card["purchase_price"] == 18.8

    tier = next(
        tier
        for company in client.get("/api/grading/companies").json()
        if company["code"] == "CGC"
        for tier in company["tiers"]
        if tier["tier_name"] == "Bulk"
    )
    assert tier["price"] == 16.80
