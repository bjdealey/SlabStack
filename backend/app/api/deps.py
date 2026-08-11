"""Shared route dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db import get_db
from app.models import Card, CardImage, CollectionGroup, GradingCompany

DbSession = Annotated[Session, Depends(get_db)]


class Pagination:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="1-indexed page number."),
        page_size: int = Query(50, ge=1, le=500, description="Rows per page."),
    ) -> None:
        self.page = page
        self.page_size = page_size


PaginationParams = Annotated[Pagination, Depends()]


def get_card(db: DbSession, card_id: Annotated[str, Path()]) -> Card:
    card = db.get(Card, card_id)
    if card is None:
        raise NotFoundError("Card", card_id)
    return card


def get_image(db: DbSession, image_id: Annotated[str, Path()]) -> CardImage:
    image = db.get(CardImage, image_id)
    if image is None:
        raise NotFoundError("Image", image_id)
    return image


def get_group(db: DbSession, group_id: Annotated[str, Path()]) -> CollectionGroup:
    group = db.get(CollectionGroup, group_id)
    if group is None:
        raise NotFoundError("Group", group_id)
    return group


def get_company(db: DbSession, company_id: Annotated[str, Path()]) -> GradingCompany:
    company = db.get(GradingCompany, company_id)
    if company is None:
        raise NotFoundError("Grading company", company_id)
    return company


CardDep = Annotated[Card, Depends(get_card)]
ImageDep = Annotated[CardImage, Depends(get_image)]
GroupDep = Annotated[CollectionGroup, Depends(get_group)]
CompanyDep = Annotated[GradingCompany, Depends(get_company)]
