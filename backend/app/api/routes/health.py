"""Health and database maintenance."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.config import settings
from app.models import Card, GradingCompany, MarketSale
from app.schemas.common import ApiModel, CountsResponse
from app.services import seed

router = APIRouter(tags=["system"])


class HealthResponse(ApiModel):
    status: str
    app: str
    version: str
    database: str
    database_ready: bool
    data_dir: str
    cards: int
    grading_companies: int
    market_sales: int
    phase: str


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health(db: DbSession) -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version="0.1.0",
        database=str(settings.db_path),
        database_ready=settings.db_path.exists(),
        data_dir=str(settings.data_dir),
        cards=db.scalar(select(func.count()).select_from(Card)) or 0,
        grading_companies=db.scalar(select(func.count()).select_from(GradingCompany)) or 0,
        market_sales=db.scalar(select(func.count()).select_from(MarketSale)) or 0,
        phase="8 — learning",
    )


@router.post(
    "/system/seed",
    response_model=CountsResponse,
    summary="Insert any missing reference data",
    description=(
        "Idempotent. Existing rows are never overwritten — your edited grading prices are "
        "more correct than the defaults."
    ),
)
def run_seed(db: DbSession) -> CountsResponse:
    return CountsResponse(counts=seed.seed_all(db))
