"""Serve the built single-page UI from the API process.

In development Vite serves the UI and proxies ``/api`` here, so this does
nothing — ``resolved_static_dir`` returns ``None`` when there is no build. In a
packaged install the build exists, and SlabStack becomes one process on one
port, which is the whole point of a local-first application: one thing to start.

Two rules the fallback has to respect:

* Unmatched ``/api/...`` must still 404 as JSON. Serving ``index.html`` there
  would turn a typo into a blank page and hide the real error from the client.
* Deep links like ``/cards/abc123`` must return ``index.html`` so the router can
  take over on a hard refresh.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import ApiError

# Real files under the build root that browsers request by exact path.
_ROOT_FILES = {"favicon.svg", "favicon.ico", "robots.txt", "manifest.webmanifest"}


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = static_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/") or full_path == "api":
            raise ApiError(
                "not_found", f"No API route matches /{full_path}.", status_code=404
            )

        if full_path in _ROOT_FILES:
            candidate = static_dir / full_path
            if candidate.is_file():
                return FileResponse(candidate)

        return FileResponse(index)
