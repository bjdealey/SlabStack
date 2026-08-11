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
from sqlalchemy import inspect

from app.api.errors import register_exception_handlers
from app.api.routes import (
    analytics,
    cards,
    catalog,
    collection,
    condition,
    grading,
    groups,
    health,
    images,
    market,
    phases,
    submissions,
)
from app.api.routes import (
    settings as settings_routes,
)
from app.api.spa import mount_spa
from app.config import BACKEND_ROOT, settings
from app.db import engine, session_scope
from app.models import Base
from app.services import seed

logger = logging.getLogger("slabstack")

DESCRIPTION = """
A local-first grading and ROI decision engine for Pokémon cards.

**Phases 1-3 (this build)** — collection database, card CRUD and search, image upload,
condition assessment storage and scoring, grade probabilities per grading company,
sales import with reversible exclusion filtering, valuation/liquidity/trend, and the
`evaluate_card` envelope every later phase fills in.

Blocks that need engines from later phases report an explicit status
(`not_implemented` / `insufficient_data`) with the phase that delivers them. Nothing
returns an invented number.

All money in this API is in **major units** (e.g. `18.80`). It is stored and
calculated as integer minor units server-side.
"""


def bootstrap() -> None:
    """Create the database if it is missing and top up reference data.

    ``create_all`` is the convenience path for a fresh local install — no user
    should have to run a migration tool to open the app for the first time. It
    is followed by an Alembic *stamp* so that a database built this way is
    recorded as being at head; without it, the first ``alembic upgrade head``
    would try to create tables that already exist.

    A database Alembic already knows about is left entirely alone, so the two
    paths (app-first and migration-first) converge.
    """
    settings.ensure_directories()

    fresh = not _has_alembic_version()
    Base.metadata.create_all(bind=engine)
    if fresh:
        _stamp_alembic_head()

    with session_scope() as db:
        counts = seed.seed_all(db)
    inserted = {key: value for key, value in counts.items() if value}
    if inserted:
        logger.info("Seeded reference data: %s", inserted)


def _has_alembic_version() -> bool:
    return inspect(engine).has_table("alembic_version")


def _stamp_alembic_head() -> None:
    """Record the freshly created schema as being at the latest revision."""
    try:
        from alembic.config import Config

        from alembic import command
    except ImportError:  # pragma: no cover - alembic is an install-time dep
        logger.warning("Alembic is not installed; skipping revision stamp.")
        return

    config_path = BACKEND_ROOT / "alembic.ini"
    if not config_path.exists():  # pragma: no cover - packaged without migrations
        return

    config = Config(str(config_path))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)
    command.stamp(config, "head")
    logger.info("Stamped a new database at the latest migration.")


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
api.include_router(market.router)
api.include_router(submissions.router)
api.include_router(analytics.router)
api.include_router(phases.router)
app.include_router(api)

# Must come last: the SPA fallback is a catch-all, so every real route has to be
# registered before it.
_static_dir = settings.resolved_static_dir
if _static_dir is not None:
    mount_spa(app, _static_dir)
    logger.info("Serving the built UI from %s", _static_dir)
else:

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "app": settings.app_name,
            "docs": "/api/docs",
            "health": "/api/health",
            "ui": "http://localhost:5173",
        }
