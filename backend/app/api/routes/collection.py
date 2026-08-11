"""Collection-level endpoints: dashboard summary and filter facets."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession
from app.models import Card
from app.schemas.common import ApiModel
from app.services import cards_service, collection_service
from app.services.collection_service import CollectionSummary

router = APIRouter(prefix="/collection", tags=["collection"])


class Facets(ApiModel):
    """Distinct values actually present in the collection, for filter menus."""

    sets: list[str]
    languages: list[str]
    variants: list[str]
    rarities: list[str]
    statuses: list[str]


@router.get("/summary", response_model=CollectionSummary, summary="Dashboard summary")
def summary(db: DbSession) -> CollectionSummary:
    return collection_service.build_summary(db)


@router.get("/facets", response_model=Facets, summary="Filter options present in the collection")
def facets(db: DbSession) -> Facets:
    return Facets(
        sets=cards_service.distinct_values(db, Card.set_code),
        languages=cards_service.distinct_values(db, Card.language),
        variants=cards_service.distinct_values(db, Card.variant),
        rarities=cards_service.distinct_values(db, Card.rarity),
        statuses=cards_service.distinct_values(db, Card.status),
    )
