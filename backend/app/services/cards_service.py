"""Card queries and mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.enums import CardStatus
from app.models import Card, CardSet, CardVariant, CollectionGroupCard, ConditionAssessment
from app.services.identity import build_catalog_key

SORTABLE_FIELDS: dict[str, Any] = {
    "created_at": Card.created_at,
    "updated_at": Card.updated_at,
    "name": Card.name,
    "set_code": Card.set_code,
    "card_number": Card.card_number,
    "purchase_price": Card.purchase_price_minor,
    "quantity": Card.quantity,
    "release_date": Card.release_date,
}


@dataclass
class CardFilters:
    q: str | None = None
    set_id: str | None = None
    set_code: str | None = None
    language: str | None = None
    variant: str | None = None
    rarity: str | None = None
    pokemon: str | None = None
    status: list[str] | None = None
    is_promo: bool | None = None
    group_id: str | None = None
    decision_override: str | None = None
    has_images: bool | None = None
    has_condition: bool | None = None


def refresh_derived(card: Card) -> Card:
    """Recompute fields the user never types (currently just ``catalog_key``)."""
    card.catalog_key = build_catalog_key(
        name=card.name,
        set_code=card.set_code,
        set_name=card.set_name,
        card_number=card.card_number,
        variant=card.variant,
        language=card.language,
        printing=card.printing,
    )
    return card


def resolve_references(db: Session, card: Card) -> Card:
    """Keep the denormalised set/variant labels in step with their reference rows.

    A card can be created from free text alone; if it names a known set or
    variant we link it, and if it links one we copy the display fields down.
    """
    if card.set_id:
        card_set = db.get(CardSet, card.set_id)
        if card_set is not None:
            card.set_name = card_set.name
            card.set_code = card_set.code
    elif card.set_code:
        match = db.scalars(
            select(CardSet).where(
                func.lower(CardSet.code) == card.set_code.lower(),
                CardSet.language == card.language,
            )
        ).first()
        if match is not None:
            card.set_id = match.id
            card.set_name = match.name
    elif card.set_name:
        # A name is enough when we know the set, and filling the code in from it
        # matters more than it looks: `catalog_key` prefers the code and falls
        # back to the name, so "Evolving Skies" typed one way and "EVS" the
        # other produce two identities for one card — which never share a price,
        # a sale or a history. Only an exact, same-language name counts.
        match = db.scalars(
            select(CardSet).where(
                func.lower(CardSet.name) == card.set_name.lower(),
                CardSet.language == card.language,
            )
        ).first()
        if match is not None:
            card.set_id = match.id
            card.set_code = match.code
            card.set_name = match.name

    if card.variant_id:
        variant = db.get(CardVariant, card.variant_id)
        if variant is not None:
            card.variant = variant.name
    elif card.variant:
        match = db.scalars(
            select(CardVariant).where(func.lower(CardVariant.name) == card.variant.lower())
        ).first()
        if match is not None:
            card.variant_id = match.id
            card.variant = match.name

    return refresh_derived(card)


def apply_filters(stmt: Select, filters: CardFilters) -> Select:
    if filters.q:
        # Local collections are thousands of rows, not millions: a LIKE scan is
        # instant and avoids an FTS index that would need its own sync path.
        needle = f"%{filters.q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Card.name).like(needle),
                func.lower(func.coalesce(Card.set_name, "")).like(needle),
                func.lower(func.coalesce(Card.set_code, "")).like(needle),
                func.lower(func.coalesce(Card.card_number, "")).like(needle),
                func.lower(func.coalesce(Card.pokemon, "")).like(needle),
                func.lower(func.coalesce(Card.variant, "")).like(needle),
                func.lower(func.coalesce(Card.notes, "")).like(needle),
            )
        )
    if filters.set_id:
        stmt = stmt.where(Card.set_id == filters.set_id)
    if filters.set_code:
        stmt = stmt.where(func.lower(Card.set_code) == filters.set_code.lower())
    if filters.language:
        stmt = stmt.where(Card.language == filters.language)
    if filters.variant:
        stmt = stmt.where(func.lower(func.coalesce(Card.variant, "")) == filters.variant.lower())
    if filters.rarity:
        stmt = stmt.where(func.lower(func.coalesce(Card.rarity, "")) == filters.rarity.lower())
    if filters.pokemon:
        stmt = stmt.where(func.lower(func.coalesce(Card.pokemon, "")) == filters.pokemon.lower())
    if filters.status:
        stmt = stmt.where(Card.status.in_(filters.status))
    if filters.is_promo is not None:
        stmt = stmt.where(Card.is_promo.is_(filters.is_promo))
    if filters.decision_override:
        stmt = stmt.where(Card.decision_override == filters.decision_override)
    if filters.group_id:
        stmt = stmt.where(
            Card.id.in_(
                select(CollectionGroupCard.card_id).where(
                    CollectionGroupCard.group_id == filters.group_id
                )
            )
        )
    if filters.has_condition is not None:
        subquery = select(ConditionAssessment.card_id).where(
            ConditionAssessment.card_id == Card.id, ConditionAssessment.is_current.is_(True)
        )
        stmt = stmt.where(subquery.exists() if filters.has_condition else ~subquery.exists())
    if filters.has_images is not None:
        from app.models import CardImage  # local import avoids a cycle at module load

        subquery = select(CardImage.id).where(CardImage.card_id == Card.id)
        stmt = stmt.where(subquery.exists() if filters.has_images else ~subquery.exists())
    return stmt


def list_cards(
    db: Session,
    filters: CardFilters,
    *,
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Card], int]:
    base = apply_filters(select(Card), filters)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    column = SORTABLE_FIELDS.get(sort, Card.created_at)
    direction = column.desc() if order.lower() == "desc" else column.asc()
    # Secondary key keeps pagination stable when the primary key ties.
    stmt = base.order_by(direction, Card.id.asc()).limit(page_size).offset((page - 1) * page_size)
    return list(db.scalars(stmt)), total


def split_card(db: Session, card: Card, count: int | None = None) -> list[Card]:
    """Split a stack into individual physical rows.

    Grading is per-copy: copy A can be a 10 and copy B an 8, so anything headed
    for a submission must be its own row (spec section 4). Images, condition
    assessments and notes stay with the original row — the new rows are blank
    copies of the identity only.
    """
    if card.quantity <= 1:
        return [card]
    to_split = card.quantity if count is None else min(count, card.quantity)
    if to_split <= 1:
        return [card]

    created: list[Card] = []
    for _ in range(to_split - 1):
        clone = Card(
            name=card.name,
            set_id=card.set_id,
            set_name=card.set_name,
            set_code=card.set_code,
            card_number=card.card_number,
            variant_id=card.variant_id,
            variant=card.variant,
            language=card.language,
            printing=card.printing,
            rarity=card.rarity,
            pokemon=card.pokemon,
            card_type=card.card_type,
            is_promo=card.is_promo,
            release_date=card.release_date,
            raw_condition=card.raw_condition,
            quantity=1,
            purchase_price_minor=card.purchase_price_minor,
            purchase_currency=card.purchase_currency,
            purchase_date=card.purchase_date,
            status=card.status or CardStatus.IN_COLLECTION.value,
            notes=card.notes,
            external_ids=card.external_ids,
        )
        refresh_derived(clone)
        db.add(clone)
        created.append(clone)

    card.quantity = card.quantity - (to_split - 1)
    db.flush()
    return [card, *created]


def current_condition(db: Session, card_id: str) -> ConditionAssessment | None:
    return db.scalars(
        select(ConditionAssessment)
        .where(ConditionAssessment.card_id == card_id, ConditionAssessment.is_current.is_(True))
        .order_by(ConditionAssessment.assessed_at.desc())
    ).first()


def distinct_values(db: Session, column: Any) -> list[str]:
    """Distinct non-empty values of a card column, for filter dropdowns."""
    rows = db.scalars(select(column).where(column.is_not(None)).distinct().order_by(column))
    return [value for value in rows if value]
