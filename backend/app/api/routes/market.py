"""Sales, listings, valuations and the recompute that ties them together.

Sales are keyed by ``catalog_key`` rather than by card, so two copies of the
same card share one market history. Routes are offered per card as a
convenience — ``/cards/{card_id}/market/...`` resolves to the card's identity —
and per identity for the cases where no card is in hand.

Recompute is explicit and synchronous. Adding a sale does not reprice a card in
some background job: the routes that change the evidence recompute as part of
the same request and return the new prices, so what the user is looking at is
always what the database holds.

Nothing here reaches the network. Every number is computed from rows already in
the local database, which is the whole point of the local database being the
source of truth.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import CardDep, DbSession
from app.api.errors import ApiError, NotFoundError
from app.enums import SaleExclusionReason
from app.models import (
    Card,
    DataSource,
    GradingCompany,
    MarketListing,
    MarketPrice,
    MarketSale,
    PriceSnapshot,
)
from app.money import to_major, to_minor
from app.schemas.common import Acknowledgement
from app.schemas.market import (
    CsvImportRequest,
    ImportResult,
    LiquidityOut,
    ListingOut,
    MarketSummary,
    PriceOut,
    PriceOverride,
    ReclassifyResult,
    RowErrorOut,
    SaleCreate,
    SaleExclusionWrite,
    SaleOut,
    SaleUpdate,
    SnapshotPoint,
    SnapshotSeries,
    TrendOut,
)
from app.services import market_service, sales_import, settings_service
from app.services.identity import grade_label as build_grade_label
from app.services.market_service import MarketParameters

router = APIRouter(tags=["market"])

HORIZON_FIELDS: dict[int, str] = {
    7: "change_7d_pct",
    30: "change_30d_pct",
    90: "change_90d_pct",
    180: "change_180d_pct",
    365: "change_365d_pct",
}


# --- Helpers -----------------------------------------------------------------


def _context(card: Card) -> sales_import.SaleContext:
    if not card.catalog_key:
        raise ApiError(
            "no_catalog_key",
            "This card has no catalog key, so its sales cannot be matched to an identity. "
            "Re-saving the card generates one.",
        )
    return sales_import.SaleContext(
        catalog_key=card.catalog_key,
        language=card.language,
        variant=card.variant,
        printing=card.printing,
    )


def _params_and_currency(db: DbSession) -> tuple[MarketParameters, str]:
    values = settings_service.get_all(db)
    return MarketParameters.from_settings(values), values.get("currency", "GBP")


def _source_id(db: DbSession, code: str) -> str | None:
    source = db.scalar(select(DataSource).where(DataSource.code == code))
    return source.id if source else None


def _price_rows(db: DbSession, catalog_key: str) -> list[PriceOut]:
    prices = market_service.prices_for(db, catalog_key)
    raw = market_service.raw_price(prices)
    return [
        PriceOut.from_model(
            row,
            None if row.grade_label == "raw" else market_service.premium_vs_raw_pct(raw, row),
        )
        for row in prices
    ]


def _liquidity_out(liquidity: market_service.Liquidity) -> LiquidityOut:
    return LiquidityOut(
        score=liquidity.score,
        band=liquidity.band,
        sales_7d=liquidity.sales_7d,
        sales_30d=liquidity.sales_30d,
        sales_90d=liquidity.sales_90d,
        sales_365d=liquidity.sales_365d,
        days_since_last_sale=liquidity.days_since_last_sale,
        active_listings=liquidity.active_listings,
        sold_to_active_ratio=liquidity.sold_to_active_ratio,
        median_days_between_sales=liquidity.median_days_between_sales,
        sales_per_month=liquidity.sales_per_month,
    )


def _trend_out(trend: market_service.Trend) -> TrendOut:
    out = TrendOut(
        direction=trend.direction,
        confidence=trend.confidence,
        sample_size=trend.sample_size,
        grade_label=trend.grade_label,
    )
    for horizon, field_name in HORIZON_FIELDS.items():
        setattr(out, field_name, trend.changes.get(horizon))
    return out


def _summary_out(db: DbSession, summary: market_service.MarketSummary) -> MarketSummary:
    return MarketSummary(
        catalog_key=summary.catalog_key,
        currency=summary.currency,
        prices=_price_rows(db, summary.catalog_key) if summary.catalog_key else [],
        liquidity=_liquidity_out(summary.liquidity),
        trend=_trend_out(summary.trend),
        sale_count=summary.sale_count,
        excluded_count=summary.excluded_count,
        grade_labels=summary.grade_labels,
        computed_at=summary.computed_at,
    )


def _recompute(db: DbSession, catalog_key: str) -> tuple[list[PriceOut], int]:
    """Re-fence outliers, then reprice. Returns the new prices and how many were flagged."""
    params, currency = _params_and_currency(db)
    outliers = sales_import.mark_outliers(db, catalog_key, params=params)
    market_service.recompute_key(db, catalog_key, params=params, currency=currency)
    return _price_rows(db, catalog_key), outliers["flagged"]


def _resolve_label(
    db: DbSession, *, company_id: str | None, grade: float | None, grade_label: str | None
) -> tuple[str, float | None, str | None]:
    """Work out ``(grade_label, grade, company_id)`` for a posted sale."""
    if company_id:
        company = db.get(GradingCompany, company_id)
        if company is None:
            raise NotFoundError("Grading company", company_id)
        if grade is None:
            raise ApiError(
                "missing_grade",
                "A grading company was given without a grade. A slab is a company and a "
                "number; either send both or send neither for a raw sale.",
            )
        return build_grade_label(company.code, grade), grade, company.id
    if grade_label and grade_label != "raw":
        return grade_label, grade, None
    return "raw", None, None


# --- Sales -------------------------------------------------------------------


def _list_sales(
    db: DbSession, catalog_key: str, include_excluded: bool, grade_label: str | None
) -> list[SaleOut]:
    stmt = select(MarketSale).where(MarketSale.catalog_key == catalog_key)
    if not include_excluded:
        stmt = stmt.where(MarketSale.is_excluded.is_(False))
    if grade_label:
        stmt = stmt.where(MarketSale.grade_label == grade_label)
    rows = db.scalars(stmt.order_by(MarketSale.sale_date.desc()))
    return [SaleOut.from_model(row) for row in rows]


@router.get(
    "/cards/{card_id}/market/sales",
    response_model=list[SaleOut],
    summary="Comparable sales for this card's identity",
    description=(
        "Includes excluded sales by default, so every automatic decision is visible and "
        "reversible. Pass `include_excluded=false` for only the ones feeding the valuation."
    ),
)
def list_card_sales(
    db: DbSession,
    card: CardDep,
    include_excluded: bool = True,
    grade_label: str | None = None,
) -> list[SaleOut]:
    if not card.catalog_key:
        return []
    return _list_sales(db, card.catalog_key, include_excluded, grade_label)


@router.get(
    "/market/sales",
    response_model=list[SaleOut],
    summary="Comparable sales for an identity",
)
def list_identity_sales(
    db: DbSession,
    catalog_key: str = Query(description="The card identity whose sales to list."),
    include_excluded: bool = True,
    grade_label: str | None = None,
) -> list[SaleOut]:
    return _list_sales(db, catalog_key, include_excluded, grade_label)


@router.post(
    "/cards/{card_id}/market/sales",
    response_model=SaleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a comparable sale",
    description=(
        "Stores one sale against the card's identity and reprices. The exclusion heuristics "
        "run unless `apply_filters` is false — a sale typed in by hand is usually one you have "
        "already looked at."
    ),
)
def create_sale(db: DbSession, card: CardDep, payload: SaleCreate) -> SaleOut:
    context = _context(card)
    _, currency = _params_and_currency(db)
    label, grade, company_id = _resolve_label(
        db,
        company_id=payload.company_id,
        grade=payload.grade,
        grade_label=payload.grade_label,
    )

    sale = MarketSale(
        catalog_key=context.catalog_key,
        card_id=card.id,
        company_id=company_id,
        grade=grade,
        grade_label=label,
        platform=payload.platform,
        sale_date=payload.sale_date,
        sale_price_minor=to_minor(payload.sale_price) or 0,
        currency=payload.currency or currency,
        shipping_minor=to_minor(payload.shipping),
        condition_note=payload.condition_note,
        listing_title=payload.listing_title,
        source_url=payload.source_url,
        seller=payload.seller,
        bid_count=payload.bid_count,
        lot_size=payload.lot_size,
        is_auction=payload.is_auction,
        external_id=payload.external_id,
        source_id=_source_id(db, "manual"),
    )

    if payload.apply_filters:
        verdict = sales_import.classify(
            title=payload.listing_title,
            context=context,
            lot_size=payload.lot_size,
            grade_label=label,
        )
        if verdict is not None:
            sale.is_excluded = True
            sale.exclusion_reason = verdict[0]
            sale.excluded_by = "system"

    db.add(sale)
    db.flush()
    _recompute(db, context.catalog_key)
    return SaleOut.from_model(sale)


@router.patch(
    "/market/sales/{sale_id}",
    response_model=SaleOut,
    summary="Correct a stored sale",
)
def update_sale(db: DbSession, sale_id: str, payload: SaleUpdate) -> SaleOut:
    sale = db.get(MarketSale, sale_id)
    if sale is None:
        raise NotFoundError("Sale", sale_id)

    data = payload.model_dump(exclude_unset=True)
    price = data.pop("sale_price", None)
    if price is not None:
        sale.sale_price_minor = to_minor(price) or sale.sale_price_minor
    if "shipping" in data:
        sale.shipping_minor = to_minor(data.pop("shipping"))

    if {"company_id", "grade", "grade_label"} & data.keys():
        sale.grade_label, sale.grade, sale.company_id = _resolve_label(
            db,
            company_id=data.pop("company_id", sale.company_id),
            grade=data.pop("grade", sale.grade),
            grade_label=data.pop("grade_label", sale.grade_label),
        )

    for key, value in data.items():
        setattr(sale, key, value)
    db.flush()
    _recompute(db, sale.catalog_key)
    return SaleOut.from_model(sale)


@router.put(
    "/market/sales/{sale_id}/exclusion",
    response_model=SaleOut,
    summary="Include or exclude a sale by hand",
    description=(
        "Your decision outranks the heuristics and survives re-imports and reclassification. "
        "Nothing is deleted: the sale keeps its row either way."
    ),
)
def set_sale_exclusion(db: DbSession, sale_id: str, payload: SaleExclusionWrite) -> SaleOut:
    sale = db.get(MarketSale, sale_id)
    if sale is None:
        raise NotFoundError("Sale", sale_id)
    if payload.reason and payload.reason not in SaleExclusionReason.values():
        raise ApiError("invalid_reason", f"reason must be one of {SaleExclusionReason.values()}")
    sales_import.set_exclusion(db, sale, excluded=payload.excluded, reason=payload.reason)
    _recompute(db, sale.catalog_key)
    return SaleOut.from_model(sale)


@router.delete(
    "/market/sales/{sale_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a sale",
    description="For rows entered in error. To stop a sale counting, exclude it instead.",
)
def delete_sale(db: DbSession, sale_id: str) -> None:
    sale = db.get(MarketSale, sale_id)
    if sale is None:
        raise NotFoundError("Sale", sale_id)
    catalog_key = sale.catalog_key
    db.delete(sale)
    db.flush()
    _recompute(db, catalog_key)


# --- Import ------------------------------------------------------------------


@router.post(
    "/cards/{card_id}/market/sales/import",
    response_model=ImportResult,
    summary="Import sold listings from CSV",
    description=(
        "Column names are matched loosely, so most marketplace exports import unchanged; a "
        "sale date and a price are the only required columns. Rows are deduplicated on "
        "`(source, external_id)`, so re-importing an overlapping export updates rather than "
        "doubles. Bad rows are reported by line number and the rest still import."
    ),
)
def import_sales_csv(db: DbSession, card: CardDep, payload: CsvImportRequest) -> ImportResult:
    context = _context(card)
    _, currency = _params_and_currency(db)

    rows, errors = sales_import.parse_csv(payload.csv, day_first=payload.day_first)
    report = sales_import.import_rows(
        db,
        rows,
        context=context,
        source_code="csv",
        card_id=card.id,
        default_currency=currency,
        apply_filters=payload.apply_filters,
    )
    prices, outliers = _recompute(db, context.catalog_key)

    return ImportResult(
        imported=report.imported,
        updated=report.updated,
        skipped=report.skipped,
        excluded=report.excluded,
        outliers_flagged=outliers,
        exclusions=report.exclusions,
        errors=[
            RowErrorOut(line_number=error.line_number, message=error.message, values=error.values)
            for error in (*errors, *report.errors)
        ],
        prices=prices,
    )


@router.post(
    "/cards/{card_id}/market/reclassify",
    response_model=ReclassifyResult,
    summary="Re-run the exclusion filters",
    description=(
        "Use after editing a card's language or variant, which changes what counts as a "
        "mismatched comparable. Sales you included or excluded yourself are left alone."
    ),
)
def reclassify(db: DbSession, card: CardDep) -> ReclassifyResult:
    context = _context(card)
    params, _ = _params_and_currency(db)
    counts = sales_import.reclassify_key(db, context=context)
    outliers = sales_import.mark_outliers(db, context.catalog_key, params=params)
    _recompute(db, context.catalog_key)
    return ReclassifyResult(
        kept=counts["kept"],
        excluded=counts["excluded"],
        unchanged=counts["unchanged"],
        skipped_user=counts["skipped_user"],
        outliers_flagged=outliers["flagged"],
        outliers_cleared=outliers["cleared"],
    )


# --- Valuations --------------------------------------------------------------


@router.get(
    "/cards/{card_id}/market",
    response_model=MarketSummary,
    summary="Market picture for this card",
)
def card_market(db: DbSession, card: CardDep) -> MarketSummary:
    params, currency = _params_and_currency(db)
    return _summary_out(
        db, market_service.summarise(db, card.catalog_key, params=params, currency=currency)
    )


@router.post(
    "/cards/{card_id}/market/recompute",
    response_model=MarketSummary,
    summary="Recompute this card's valuations from stored sales",
    description=(
        "Re-fences price outliers and rewrites every grade's valuation. Also writes today's "
        "`price_snapshots` row, so a price history accrues whether or not a provider is "
        "connected. Reads only local data — nothing leaves the machine."
    ),
)
def recompute_card_market(db: DbSession, card: CardDep) -> MarketSummary:
    context = _context(card)
    _recompute(db, context.catalog_key)
    params, currency = _params_and_currency(db)
    return _summary_out(
        db, market_service.summarise(db, context.catalog_key, params=params, currency=currency)
    )


@router.get(
    "/market/prices",
    response_model=list[PriceOut],
    summary="Computed valuations for one identity",
)
def market_prices(
    db: DbSession,
    catalog_key: str = Query(description="The card identity to value."),
) -> list[PriceOut]:
    return _price_rows(db, catalog_key)


@router.put(
    "/market/prices/{price_id}/override",
    response_model=PriceOut,
    summary="Set a value yourself",
    description=(
        "Stored in `user_value`, alongside the computed figure rather than over it, so both "
        "stay visible. Send a null value to clear it."
    ),
)
def override_price(db: DbSession, price_id: str, payload: PriceOverride) -> PriceOut:
    row = db.get(MarketPrice, price_id)
    if row is None:
        raise NotFoundError("Market price", price_id)
    row.user_value_minor = payload.value_minor()
    row.user_value_note = payload.note
    db.flush()
    prices = market_service.prices_for(db, row.catalog_key)
    premium = (
        None
        if row.grade_label == "raw"
        else market_service.premium_vs_raw_pct(market_service.raw_price(prices), row)
    )
    return PriceOut.from_model(row, premium)


@router.get(
    "/cards/{card_id}/market/history",
    response_model=list[SnapshotSeries],
    summary="Stored price history for this card",
    description=(
        "One series per grade, from `price_snapshots`. This is the user's own history, and the "
        "one thing that cannot be re-fetched if a data source disappears."
    ),
)
def card_history(
    db: DbSession, card: CardDep, days: int = Query(365, ge=1, le=3650)
) -> list[SnapshotSeries]:
    if not card.catalog_key:
        return []
    cutoff = date.today() - timedelta(days=days)
    rows = db.scalars(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.catalog_key == card.catalog_key,
            PriceSnapshot.snapshot_date >= cutoff,
        )
        .order_by(PriceSnapshot.grade_label, PriceSnapshot.snapshot_date)
    )

    series: dict[str, SnapshotSeries] = {}
    for row in rows:
        bucket = series.setdefault(
            row.grade_label, SnapshotSeries(grade_label=row.grade_label, currency=row.currency)
        )
        bucket.points.append(
            SnapshotPoint(
                snapshot_date=row.snapshot_date,
                value=to_major(row.value_minor) or 0.0,
                sample_size=row.sample_size,
                active_listings=row.active_listings,
            )
        )
    return list(series.values())


@router.get(
    "/cards/{card_id}/market/listings",
    response_model=list[ListingOut],
    summary="Active listings for this card's identity",
)
def card_listings(db: DbSession, card: CardDep) -> list[ListingOut]:
    if not card.catalog_key:
        return []
    rows = db.scalars(
        select(MarketListing)
        .where(
            MarketListing.catalog_key == card.catalog_key,
            MarketListing.is_active.is_(True),
        )
        .order_by(MarketListing.price_minor)
    )
    return [ListingOut.from_model(row) for row in rows]


@router.post(
    "/market/recompute-all",
    response_model=Acknowledgement,
    summary="Recompute every card's valuations",
    description=(
        "Walks every identity in the collection. Run after changing the valuation settings "
        "(window, half-life, outlier fence) or after importing in bulk."
    ),
)
def recompute_all(db: DbSession) -> Acknowledgement:
    params, currency = _params_and_currency(db)
    keys = list(
        db.scalars(select(Card.catalog_key).where(Card.catalog_key.is_not(None)).distinct())
    )
    for key in keys:
        sales_import.mark_outliers(db, key, params=params)
        market_service.recompute_key(db, key, params=params, currency=currency)
    return Acknowledgement(ok=True, message=f"Recomputed {len(keys)} card identities.")
