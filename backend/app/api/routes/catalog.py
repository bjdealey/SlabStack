"""Sets and variants — the reference catalogue behind card entry."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import DbSession
from app.api.errors import ConflictError, NotFoundError
from app.models import Card, CardSet, CardVariant
from app.schemas.card import CardSetOut, CardSetWrite, CardVariantOut, CardVariantWrite

router = APIRouter(tags=["catalog"])


@router.get("/sets", response_model=list[CardSetOut], summary="List sets")
def list_sets(
    db: DbSession,
    q: Annotated[str | None, Query(description="Match on set name or code.")] = None,
    language: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[CardSetOut]:
    stmt = select(CardSet)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(CardSet.name).like(needle), func.lower(CardSet.code).like(needle))
        )
    if language:
        stmt = stmt.where(CardSet.language == language)
    stmt = stmt.order_by(CardSet.release_date.desc().nullslast(), CardSet.name).limit(limit)
    return [CardSetOut.model_validate(row) for row in db.scalars(stmt)]


@router.post("/sets", response_model=CardSetOut, status_code=status.HTTP_201_CREATED, summary="Add a set")
def create_set(db: DbSession, payload: CardSetWrite) -> CardSetOut:
    existing = db.scalars(
        select(CardSet).where(
            func.lower(CardSet.code) == payload.code.lower(), CardSet.language == payload.language
        )
    ).first()
    if existing is not None:
        raise ConflictError(f"Set '{payload.code}' already exists for {payload.language}.")
    card_set = CardSet(**payload.model_dump())
    db.add(card_set)
    db.flush()
    return CardSetOut.model_validate(card_set)


@router.patch("/sets/{set_id}", response_model=CardSetOut, summary="Update a set")
def update_set(db: DbSession, set_id: str, payload: CardSetWrite) -> CardSetOut:
    card_set = db.get(CardSet, set_id)
    if card_set is None:
        raise NotFoundError("Set", set_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(card_set, field, value)
    db.flush()
    return CardSetOut.model_validate(card_set)


@router.delete("/sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a set")
def delete_set(db: DbSession, set_id: str) -> Response:
    card_set = db.get(CardSet, set_id)
    if card_set is None:
        raise NotFoundError("Set", set_id)
    in_use = db.scalar(select(func.count()).select_from(Card).where(Card.set_id == set_id)) or 0
    if in_use:
        raise ConflictError(
            f"{in_use} card(s) still reference this set.",
            {"cards": in_use},
        )
    db.delete(card_set)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/variants", response_model=list[CardVariantOut], summary="List card variants")
def list_variants(db: DbSession, include_inactive: bool = False) -> list[CardVariantOut]:
    stmt = select(CardVariant).order_by(CardVariant.sort_order, CardVariant.name)
    if not include_inactive:
        stmt = stmt.where(CardVariant.active.is_(True))
    return [CardVariantOut.model_validate(row) for row in db.scalars(stmt)]


@router.post(
    "/variants",
    response_model=CardVariantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a variant",
)
def create_variant(db: DbSession, payload: CardVariantWrite) -> CardVariantOut:
    if db.scalars(select(CardVariant).where(CardVariant.code == payload.code)).first():
        raise ConflictError(f"Variant '{payload.code}' already exists.")
    variant = CardVariant(**payload.model_dump())
    db.add(variant)
    db.flush()
    return CardVariantOut.model_validate(variant)


@router.patch("/variants/{variant_id}", response_model=CardVariantOut, summary="Update a variant")
def update_variant(db: DbSession, variant_id: str, payload: CardVariantWrite) -> CardVariantOut:
    variant = db.get(CardVariant, variant_id)
    if variant is None:
        raise NotFoundError("Variant", variant_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(variant, field, value)
    db.flush()
    return CardVariantOut.model_validate(variant)


@router.delete(
    "/variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a variant"
)
def delete_variant(db: DbSession, variant_id: str) -> Response:
    variant = db.get(CardVariant, variant_id)
    if variant is None:
        raise NotFoundError("Variant", variant_id)
    if variant.is_builtin:
        raise ConflictError("Built-in variants cannot be deleted. Deactivate it instead.")
    db.delete(variant)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
