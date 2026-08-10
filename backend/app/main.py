"""SlabStack API.

Local-first by construction: binds to localhost, stores everything in one SQLite
file plus a media directory, has no authentication because there is no remote
user, and makes no outbound network call anywhere in Phase 1.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routes import (
    cards,
    catalog,
    collection,
    condition,
    grading,
    groups,
    health,
    images,
    phases,
)
from app.api.routes import (
    settings as settings_routes,
)
from app.config import settings
from app.db import engine, session_scope
from app.models import Base
from app.services import seed

logger = logging.getLogger("slabstack")

DESCRIPTION = """
A local-first grading and ROI decision engine for Pokémon cards.

**Phase 1 (this build)** — collection database, card CRUD and search, image upload,
condition assessment storage and scoring, grading/selling configuration, and the
`evaluate_card` envelope every later phase fills in.

Blocks that need engines from later phases report an explicit status
(`not_implemented` / `insufficient_data`) with the phase that delivers them. Nothing
returns an invented number.

All money in this API is in **major units** (e.g. `18.80`). It is stored and
calculated as integer minor units server-side.
"""


def bootstrap() -> None:
    """Create the database if it is missing and top up reference data.

    ``create_all`` is a convenience for a fresh local install; Alembic owns
    schema changes from there (``alembic upgrade head``).
    """
    settings.ensure_directories()
    Base.metadata.create_all(bind=engine)
    with session_scope() as db:
        counts = seed.seed_all(db)
    inserted = {key: value for key, value in counts.items() if value}
    if inserted:
        logger.info("Seeded reference data: %s", inserted)


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    yield


app = FastAPI(
    title="SlabStack API",
    version="0.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

api = APIRouter(prefix="/api")
api.include_router(health.router)
api.include_router(settings_routes.router)
api.include_router(catalog.router)
api.include_router(collection.router)
api.include_router(cards.router)
api.include_router(images.router)
api.include_router(condition.router)
api.include_router(groups.router)
api.include_router(grading.router)
api.include_router(phases.router)
app.include_router(api)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "app": settings.app_name,
        "docs": "/api/docs",
        "health": "/api/health",
        "ui": "http://localhost:5173",
    }
