"""Collection groups: folders and watchlists."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.api.deps import DbSession, GroupDep
from app.api.errors import ConflictError, NotFoundError
from app.models import Card, CollectionGroup, CollectionGroupCard
from app.schemas.card import GroupCardsRequest, GroupOut, GroupWrite
from app.schemas.common import Acknowledgement

router = APIRouter(prefix="/groups", tags=["groups"])


def _with_counts(db: DbSession, groups: list[CollectionGroup]) -> list[GroupOut]:
    counts = dict(
        db.execute(
            select(CollectionGroupCard.group_id, func.count()).group_by(
                CollectionGroupCard.group_id
            )
        ).all()
    )
    result = []
    for group in groups:
        out = GroupOut.model_validate(group)
        out.card_count = counts.get(group.id, 0)
        result.append(out)
    return result


@router.get("", response_model=list[GroupOut], summary="List groups")
def list_groups(db: DbSession) -> list[GroupOut]:
    groups = list(
        db.scalars(select(CollectionGroup).order_by(CollectionGroup.sort_order, CollectionGroup.name))
    )
    return _with_counts(db, groups)


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED, summary="Create a group")
def create_group(db: DbSession, payload: GroupWrite) -> GroupOut:
    if db.scalars(select(CollectionGroup).where(CollectionGroup.name == payload.name)).first():
        raise ConflictError(f"A group named '{payload.name}' already exists.")
    group = CollectionGroup(**payload.model_dump())
    db.add(group)
    db.flush()
    return _with_counts(db, [group])[0]


@router.patch("/{group_id}", response_model=GroupOut, summary="Update a group")
def update_group(db: DbSession, group: GroupDep, payload: GroupWrite) -> GroupOut:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    db.flush()
    return _with_counts(db, [group])[0]


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a group")
def delete_group(db: DbSession, group: GroupDep) -> Response:
    db.delete(group)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{group_id}/cards", response_model=Acknowledgement, summary="Add cards to a group")
def add_cards(db: DbSession, group: GroupDep, payload: GroupCardsRequest) -> Acknowledgement:
    existing = set(
        db.scalars(
            select(CollectionGroupCard.card_id).where(CollectionGroupCard.group_id == group.id)
        )
    )
    added = 0
    for card_id in payload.card_ids:
        if card_id in existing:
            continue
        if db.get(Card, card_id) is None:
            raise NotFoundError("Card", card_id)
        db.add(CollectionGroupCard(group_id=group.id, card_id=card_id))
        added += 1
    db.flush()
    return Acknowledgement(message=f"Added {added} card(s) to {group.name}.")


@router.delete(
    "/{group_id}/cards/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a card from a group",
)
def remove_card(db: DbSession, group: GroupDep, card_id: str) -> Response:
    link = db.scalars(
        select(CollectionGroupCard).where(
            CollectionGroupCard.group_id == group.id, CollectionGroupCard.card_id == card_id
        )
    ).first()
    if link is None:
        raise NotFoundError("Card in group", card_id)
    db.delete(link)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
