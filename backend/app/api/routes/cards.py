"""Card CRUD, search and evaluation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from app.api.deps import CardDep, DbSession, PaginationParams
from app.api.errors import ApiError
from app.models import Card, ConditionAssessment
from app.schemas.card import (
    BulkCardCreate,
    CardCreate,
    CardOut,
    CardSplitRequest,
    CardUpdate,
    apply_card_payload,
)
from app.schemas.common import Page
from app.schemas.evaluation import CardEvaluation
from app.services import (
    cards_service,
    evaluation,
    market_service,
    sales_import,
    settings_service,
)
from app.services.cards_service import CardFilters

router = APIRouter(prefix="/cards", tags=["cards"])


def _assessed_ids(db: DbSession, cards: list[Card]) -> set[str]:
    if not cards:
        return set()
    rows = db.scalars(
        select(ConditionAssessment.card_id).where(
            ConditionAssessment.card_id.in_([card.id for card in cards]),
            ConditionAssessment.is_current.is_(True),
        )
    )
    return set(rows)


@router.get("", response_model=Page[CardOut], summary="Search and list cards")
def list_cards(
    db: DbSession,
    pagination: PaginationParams,
    q: Annotated[str | None, Query(description="Free text over name, set, number, notes.")] = None,
    set_id: str | None = None,
    set_code: str | None = None,
    language: str | None = None,
    variant: str | None = None,
    rarity: str | None = None,
    pokemon: str | None = None,
    status_in: Annotated[list[str] | None, Query(alias="status")] = None,
    is_promo: bool | None = None,
    group_id: str | None = None,
    decision_override: str | None = None,
    has_images: bool | None = None,
    has_condition: bool | None = None,
    sort: str = "created_at",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> Page[CardOut]:
    filters = CardFilters(
        q=q,
        set_id=set_id,
        set_code=set_code,
        language=language,
        variant=variant,
        rarity=rarity,
        pokemon=pokemon,
        status=status_in,
        is_promo=is_promo,
        group_id=group_id,
        decision_override=decision_override,
        has_images=has_images,
        has_condition=has_condition,
    )
    cards, total = cards_service.list_cards(
        db,
        filters,
        sort=sort,
        order=order,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    assessed = _assessed_ids(db, cards)
    items = [
        CardOut.from_model(card, has_condition_assessment=card.id in assessed) for card in cards
    ]
    return Page.build(items, total, pagination.page, pagination.page_size)


@router.post("", response_model=CardOut, status_code=status.HTTP_201_CREATED, summary="Add a card")
def create_card(db: DbSession, payload: CardCreate) -> CardOut:
    card = Card()
    apply_card_payload(card, payload.model_dump())
    cards_service.resolve_references(db, card)
    db.add(card)
    db.flush()
    return CardOut.from_model(card)


@router.post(
    "/bulk",
    response_model=list[CardOut],
    status_code=status.HTTP_201_CREATED,
    summary="Add many cards at once",
)
def create_cards_bulk(db: DbSession, payload: BulkCardCreate) -> list[CardOut]:
    created: list[Card] = []
    for item in payload.cards:
        card = Card()
        apply_card_payload(card, item.model_dump())
        cards_service.resolve_references(db, card)
        db.add(card)
        created.append(card)
    db.flush()
    return [CardOut.from_model(card) for card in created]


@router.get("/{card_id}", response_model=CardOut, summary="Get one card")
def get_card(db: DbSession, card: CardDep) -> CardOut:
    assessed = cards_service.current_condition(db, card.id) is not None
    return CardOut.from_model(card, has_condition_assessment=assessed)


@router.patch(
    "/{card_id}",
    response_model=CardOut,
    summary="Update a card",
    description=(
        "Editing the name, number, set, variant, language or printing changes the card's "
        "identity. Sales recorded against this card follow it and are re-judged against the "
        "new identity, so a corrected language does not leave the market history stranded — "
        "nor quietly count English comparables toward a Japanese card."
    ),
)
def update_card(db: DbSession, card: CardDep, payload: CardUpdate) -> CardOut:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return CardOut.from_model(card)
    previous_key = card.catalog_key
    apply_card_payload(card, changes)
    cards_service.resolve_references(db, card)
    db.flush()
    if previous_key and card.catalog_key and previous_key != card.catalog_key:
        _follow_identity_change(db, card, previous_key)
    assessed = cards_service.current_condition(db, card.id) is not None
    return CardOut.from_model(card, has_condition_assessment=assessed)


def _follow_identity_change(db: DbSession, card: Card, previous_key: str) -> None:
    """Move this card's own market rows onto its new identity and re-filter them."""
    moved = sales_import.migrate_card_key(db, card.id, previous_key, card.catalog_key)
    if not moved:
        return
    context = sales_import.SaleContext(
        catalog_key=card.catalog_key,
        language=card.language,
        variant=card.variant,
        printing=card.printing,
    )
    sales_import.reclassify_key(db, context=context)
    values = settings_service.get_all(db)
    params = market_service.MarketParameters.from_settings(values)
    sales_import.mark_outliers(db, card.catalog_key, params=params)
    market_service.recompute_key(
        db, card.catalog_key, params=params, currency=values.get("currency", "GBP")
    )


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a card")
def delete_card(db: DbSession, card: CardDep) -> Response:
    from app.services import images_service

    # Files first: a card row without its images is recoverable, an orphaned
    # pile of JPEGs on disk is not. The rows go with the cascade.
    images_service.delete_card_media(card)
    db.delete(card)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{card_id}/split",
    response_model=list[CardOut],
    summary="Split a stack into individual physical cards",
)
def split_card(db: DbSession, card: CardDep, payload: CardSplitRequest | None = None) -> list[CardOut]:
    if card.quantity <= 1:
        raise ApiError(
            "cannot_split",
            "This card is already a single copy. Grading decisions are made per physical card.",
        )
    count = payload.count if payload else None
    cards = cards_service.split_card(db, card, count)
    return [CardOut.from_model(item) for item in cards]


@router.get(
    "/{card_id}/evaluation",
    response_model=CardEvaluation,
    summary="Evaluate a card: condition, market, economics, recommendation",
    description=(
        "The central decision-engine call (spec section 45). Every block carries a status; "
        "blocks whose engine has not been built yet report `not_implemented` or "
        "`insufficient_data` with the phase that delivers them, never a fabricated number.\n\n"
        "`batch_size` is how many cards to assume share a submission's shipping and insurance. "
        "It defaults to 1 — the honest worst case, where one card carries the whole postage — "
        "and changes which tiers are usable as well as what each one costs."
    ),
)
def evaluate(
    db: DbSession,
    card: CardDep,
    batch_size: Annotated[
        int,
        Query(ge=1, le=1000, description="Cards assumed to share one submission."),
    ] = 1,
) -> CardEvaluation:
    return evaluation.evaluate_card(db, card, batch_size=batch_size)
