"""Serving the built UI from the API process.

The failure this guards against is subtle: a catch-all that swallows unmatched
``/api`` routes turns a client typo into a blank page instead of a JSON 404.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def packaged_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A build of the UI, minimal but shaped like the real one."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>SlabStack</title>", encoding="utf-8")
    (static / "assets" / "index-abc123.js").write_text("console.log('ui')", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")

    monkeypatch.setenv("SLABSTACK_STATIC_DIR", str(static))

    # config caches its Settings, and main wires the SPA at import time.
    from app import config

    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "settings", config.get_settings())

    import app.main

    module = importlib.reload(app.main)
    app_instance: FastAPI = module.app
    with TestClient(app_instance) as client:
        yield client

    config.get_settings.cache_clear()


def test_root_serves_the_ui(packaged_app: TestClient):
    response = packaged_app.get("/")
    assert response.status_code == 200
    assert "SlabStack" in response.text


def test_deep_link_serves_the_ui_so_refresh_works(packaged_app: TestClient):
    response = packaged_app.get("/cards/9f2c4a")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()


def test_assets_are_served(packaged_app: TestClient):
    assert packaged_app.get("/assets/index-abc123.js").status_code == 200


def test_root_files_are_served(packaged_app: TestClient):
    assert packaged_app.get("/favicon.svg").status_code == 200


def test_api_still_works(packaged_app: TestClient):
    response = packaged_app.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unmatched_api_route_404s_as_json_not_html(packaged_app: TestClient):
    response = packaged_app.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "<!doctype" not in response.text.lower()


def test_docs_are_still_reachable(packaged_app: TestClient):
    assert packaged_app.get("/api/openapi.json").status_code == 200


def test_without_a_build_the_api_reports_where_the_ui_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Development: Vite serves the UI, so the root is a pointer, not a page.

    Pointed at an empty directory rather than relying on the repo not having a
    build — a developer who has run `npm run build` should not fail the suite.
    """
    monkeypatch.setenv("SLABSTACK_STATIC_DIR", str(tmp_path / "no-build"))

    from app import config

    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "settings", config.get_settings())

    import app.main

    module = importlib.reload(app.main)
    with TestClient(module.app) as client:
        body = client.get("/").json()

    config.get_settings.cache_clear()
    assert body["health"] == "/api/health"
