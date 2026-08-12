"""Fetching from a provider, and deciding what of it to keep.

The rule from Phase 1 holds and is the reason this module exists at all:
**providers only ever write into the local tables**, and nothing downstream of
the database calls a provider directly. An adapter returns records; this decides
what to persist. So a provider going away, changing its shape, or returning
nonsense costs the user future updates and never their history.

Four things it will not do:

**It will not overwrite your own numbers.** A price you set by hand, and a
valuation computed from sales you recorded, both outrank anything fetched. The
provider's row is separate and only used when there is nothing better.

**It will not invent an exchange rate.** Providers quote USD and EUR; this app
reports one currency. Where the rate to convert is not configured, the price is
*skipped and reported* rather than written as though the numbers were already in
your currency. That is the difference between a missing figure and a wrong one.

**It will not delete on failure.** A sync that errors halfway leaves everything
it had already written and records why it stopped. Nothing is cleared first and
refilled after.

**It will not pretend a partial sync was a whole one.** Every run returns what
it fetched, what it skipped, and why, per card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import Confidence
from app.models import (
    Card,
    DataSource,
    GradingCompany,
    MarketListing,
    MarketPrice,
    PriceSnapshot,
)
from app.services import market_service, sales_import, settings_service
from app.services.identity import grade_label as build_grade_label
from app.services.market_data.base import CardQuery, MarketDataProvider, MarketKey
from app.services.market_data.http import CapabilityDeniedError, ProviderRequestError
from app.services.market_data.registry import ProviderUnavailableError, load_provider
from app.services.market_service import MarketParameters

__all__ = [
    "CardSyncOutcome",
    "SyncReport",
    "convert_minor",
    "sync_cards",
    "sync_source",
]


@dataclass
class CardSyncOutcome:
    """What happened to one card in a sync run."""

    card_id: str
    name: str
    status: str
    #: Populated on success, in the app's currency.
    value: float | None = None
    currency: str | None = None
    #: What the provider quoted, before conversion, so the number is auditable.
    source_value: float | None = None
    source_currency: str | None = None
    fx_rate: float | None = None
    reason: str | None = None

    # --- Sales-level sources -------------------------------------------------
    # An aggregate source answers with one number; a marketplace answers with
    # evidence, and how much of it arrived — and how much was thrown away, and
    # for which grades — is the interesting part of the run.
    sales_imported: int = 0
    sales_updated: int = 0
    sales_excluded: int = 0
    #: Grade labels this card gained sales for, raw first. The graded ones are
    #: the point: they are what the raw price is compared against.
    grades: list[str] = field(default_factory=list)
    listings_seen: int = 0
    #: What the source says exists, when it says. ``listings_seen`` is one page.
    listings_reported: int | None = None


@dataclass
class SyncReport:
    source_code: str
    source_name: str
    started_at: str
    finished_at: str | None = None
    requested: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    status: str = "ok"
    reason: str | None = None
    cards: list[CardSyncOutcome] = field(default_factory=list)
    #: Things true of the run as a whole rather than of one card.
    notes: list[str] = field(default_factory=list)
    #: Run totals for a sales-level source. Zero for an aggregate one.
    sales_imported: int = 0
    sales_excluded: int = 0
    listings_seen: int = 0


# --- Currency ----------------------------------------------------------------


def convert_minor(
    amount_minor: int, *, from_currency: str, to_currency: str, rates: dict
) -> tuple[int | None, float | None]:
    """Convert, or refuse and say nothing rather than something wrong.

    Returns ``(None, None)`` when no rate is configured for the pair. There is
    no live FX feed in this build and there is no sensible default: a wrong rate
    silently rescales every price a provider supplies, and the mistake would be
    invisible precisely because the numbers still look like money.
    """
    if from_currency == to_currency:
        return amount_minor, 1.0

    rate = _rate_for(rates, from_currency, to_currency)
    if rate is None:
        return None, None
    return round(amount_minor * rate), rate


def _rate_for(rates: dict, from_currency: str, to_currency: str) -> float | None:
    """Direct rate, else the inverse of the opposite pair, else nothing."""
    direct = rates.get(f"{from_currency}_{to_currency}")
    if isinstance(direct, int | float) and direct > 0:
        return float(direct)

    inverse = rates.get(f"{to_currency}_{from_currency}")
    if isinstance(inverse, int | float) and inverse > 0:
        return round(1 / float(inverse), 6)
    return None


# --- Syncing -----------------------------------------------------------------


def sync_source(
    db: Session,
    source: DataSource,
    *,
    card_ids: list[str] | None = None,
    limit: int = 200,
) -> SyncReport:
    """Refresh prices for a set of cards from one source."""
    report = SyncReport(
        source_code=source.code,
        source_name=source.name,
        started_at=datetime.now(UTC).isoformat(),
    )

    try:
        provider = load_provider(source)
    except ProviderUnavailableError as exc:
        return _abort(db, source, report, str(exc))

    caps = provider.capabilities()
    if not (caps.current_price or caps.sales_history or caps.active_listings):
        return _abort(
            db,
            source,
            report,
            f"{source.name} supplies neither prices nor sales, so there is nothing to sync "
            "from it.",
        )

    cards, eligible = _cards_to_sync(db, source, provider, card_ids=card_ids, limit=limit)
    report.requested = len(cards)
    if eligible > len(cards):
        # Never a silent cap. A run that quietly covered the first 200 of 900
        # cards reads exactly like one that covered everything, and the missing
        # 700 look like cards with no market rather than cards not asked about.
        report.notes.append(
            f"{eligible} card(s) could be synced from this source and this run took the first "
            f"{len(cards)}, most recently updated first. Run it again for the rest, or raise the "
            "limit — sources are rate limited on purpose, so a whole collection takes a while."
        )
    if not cards:
        report.status = "insufficient_data"
        report.reason = (
            "No card is linked to this source yet. Look a card up in the catalogue and confirm "
            "the match — the link is stored, and future syncs use it."
            if provider.requires_external_id
            else "There is no card to look up. Add a card to your collection first."
        )
        _record(db, source, report)
        return report

    values = settings_service.get_all(db)
    currency = values.get("currency", "GBP")
    rates = values.get("fx_rates") or {}
    params = MarketParameters.from_settings(values)

    for card in cards:
        outcome = _sync_one(
            db,
            provider,
            card,
            source=source,
            currency=currency,
            rates=rates,
            params=params,
            report=report,
        )
        report.cards.append(outcome)

        if outcome.status == "updated":
            report.updated += 1
        elif outcome.status == "failed":
            report.failed += 1
        else:
            report.skipped += 1

        report.sales_imported += outcome.sales_imported + outcome.sales_updated
        report.sales_excluded += outcome.sales_excluded
        report.listings_seen += outcome.listings_seen

    _summarise(report, currency=currency, rates=rates)
    _record(db, source, report)
    return report


def _sync_one(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    source: DataSource,
    currency: str,
    rates: dict,
    params: MarketParameters,
    report: SyncReport,
) -> CardSyncOutcome:
    """Take from this source whatever it actually has for this card.

    A source is not one shape. A catalogue answers with an aggregate price; a
    marketplace answers with sales and with what is currently on sale. Both are
    worth having and one source may supply several, so this asks for each thing
    the provider claims and lets the answers accumulate on one outcome.
    """
    outcome = CardSyncOutcome(card_id=card.id, name=_display(card), status="skipped")
    if not card.catalog_key:
        outcome.reason = "No catalog key, so nothing fetched could be attached to anything."
        return outcome

    caps = provider.capabilities()
    if caps.current_price:
        _sync_price(db, provider, card, source=source, currency=currency, rates=rates,
                    outcome=outcome)
    if caps.sales_history or caps.active_listings:
        _sync_evidence(db, provider, card, source=source, currency=currency, rates=rates,
                       params=params, outcome=outcome, report=report)
    return outcome


def _sync_price(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    source: DataSource,
    currency: str,
    rates: dict,
    outcome: CardSyncOutcome,
) -> CardSyncOutcome:
    """Aggregate prices from a source that keeps an index — one row per grade.

    A grade at a time, because a source that prices slabs is the whole reason
    the grading question is answerable, and asking only for ``raw`` would leave
    it delivering exactly the half the application already had.
    """
    external_id = (card.external_ids or {}).get(source.code)

    if not external_id:
        outcome.reason = "Not linked to this source yet."
        return outcome

    written: list[str] = []
    for label in provider.available_grade_labels():
        if _sync_one_grade(
            db,
            provider,
            card,
            label=label,
            external_id=external_id,
            source=source,
            currency=currency,
            rates=rates,
            outcome=outcome,
        ):
            written.append(label)
        if outcome.status == "failed":
            # The source is unreachable or refusing; the remaining grades would
            # fail identically and only burn somebody's rate limit.
            return outcome

    if written:
        outcome.status = "updated"
        outcome.grades = written
    elif not outcome.reason:
        outcome.reason = f"{source.name} has no price for this card at any grade."
    return outcome


def _sync_one_grade(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    label: str,
    external_id: str,
    source: DataSource,
    currency: str,
    rates: dict,
    outcome: CardSyncOutcome,
) -> bool:
    key = MarketKey(
        catalog_key=card.catalog_key,
        grade_label=label,
        currency=currency,
        external_id=external_id,
        variant=card.variant,
    )

    try:
        point = provider.get_current_price(key)
    except ProviderRequestError as exc:
        outcome.status = "failed"
        outcome.reason = str(exc)
        return False
    except Exception as exc:  # One bad adapter must not abort the whole run.
        outcome.status = "failed"
        outcome.reason = f"{source.name} adapter raised {type(exc).__name__}: {exc}"
        return False

    if point is None:
        # Normal, not an error: most cards have some grade with too little
        # behind it to price. Only worth reporting when *nothing* priced.
        if label == "raw" and not outcome.reason:
            outcome.reason = f"{source.name} has no current price for this card."
        return False

    converted, rate = convert_minor(
        point.value_minor, from_currency=point.currency, to_currency=currency, rates=rates
    )
    if label == "raw":
        # The headline figures on the report describe the raw card, which is
        # what every other source reports and what keeps the two comparable.
        outcome.source_value = round(point.value_minor / 100, 2)
        outcome.source_currency = point.currency

    if converted is None:
        outcome.reason = (
            f"{point.currency} price, but no {point.currency}→{currency} rate is set. Add one in "
            "Settings → Market and run this again — nothing is lost, the price is simply not "
            "written until it can be stated in your currency."
        )
        return False

    if label == "raw":
        outcome.value = round(converted / 100, 2)
        outcome.currency = currency
        outcome.fx_rate = rate

    _write_price(
        db,
        card=card,
        source=source,
        point=point,
        value_minor=converted,
        currency=currency,
        label=label,
    )
    return True


def _write_price(
    db: Session,
    *,
    card: Card,
    source: DataSource,
    point,
    value_minor: int,
    currency: str,
    label: str = "raw",
) -> None:
    """Upsert this source's own price row for one grade, and snapshot it.

    The snapshot matters more than it looks. An index source has no price
    history to import, so the only way a trend ever appears for a
    provider-priced card is by accruing one a day at a time from here.
    ``recompute_key`` cannot do it: it walks the grades that have *sales*, and a
    provider-priced card has none.
    """
    grade, company_id = _resolve_grade(db, label)

    row = db.scalars(
        select(MarketPrice).where(
            MarketPrice.catalog_key == card.catalog_key,
            MarketPrice.grade_label == label,
            MarketPrice.source_id == source.id,
        )
    ).first()
    if row is None:
        row = MarketPrice(
            catalog_key=card.catalog_key, grade_label=label, source_id=source.id
        )
        db.add(row)

    row.grade = grade
    row.company_id = company_id
    row.currency = currency
    row.median_minor = value_minor
    row.realistic_sale_minor = value_minor
    # Left null on purpose. An aggregate index has no quartiles, no last sale
    # and no window, and filling them from the one number available would
    # manufacture a spread that does not exist.
    row.weighted_median_minor = None
    row.low_quartile_minor = None
    row.high_quartile_minor = None
    row.last_sale_minor = None
    row.quick_sale_minor = None
    row.window_days = None
    row.last_sale_at = point.as_of
    # Zero sales, and the confidence says so: this is somebody's index, not
    # evidence of what you could have sold it for.
    row.sample_size = 0
    row.confidence = Confidence.LOW.value
    row.computed_at = datetime.now(UTC)

    today = date.today()
    snapshot = db.scalars(
        select(PriceSnapshot).where(
            PriceSnapshot.catalog_key == card.catalog_key,
            PriceSnapshot.grade_label == label,
            PriceSnapshot.snapshot_date == today,
            PriceSnapshot.source_id == source.id,
        )
    ).first()
    if snapshot is None:
        db.add(
            PriceSnapshot(
                catalog_key=card.catalog_key,
                grade_label=label,
                grade=grade,
                company_id=company_id,
                snapshot_date=today,
                currency=currency,
                value_minor=value_minor,
                sample_size=0,
                source_id=source.id,
            )
        )
    else:
        # One row per source per day: syncing twice is not two data points.
        snapshot.value_minor = value_minor
        snapshot.currency = currency


def _resolve_grade(db: Session, label: str) -> tuple[float | None, str | None]:
    """Turn "PSA 10" back into the grade and the company row it belongs to.

    Every engine downstream filters graded prices by company — the best route is
    computed strictly within one grader, because pairing ACE's fee with PSA's
    slab price describes a route that does not exist. A graded row with no
    company attached would be invisible to all of it.
    """
    if not label or label.strip().lower() == "raw":
        return None, None
    parsed = sales_import.parse_grade_from_title(label)
    if parsed is None:
        return None, None
    code, grade = parsed
    company = db.scalars(
        select(GradingCompany).where(func.upper(GradingCompany.code) == code.upper())
    ).first()
    return grade, (company.id if company else None)


# --- Sales-level sources -----------------------------------------------------


def _sync_evidence(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    source: DataSource,
    currency: str,
    rates: dict,
    params: MarketParameters,
    outcome: CardSyncOutcome,
    report: SyncReport,
) -> None:
    """Individual sales, and what is on sale right now.

    The difference from a price sync is worth stating: nothing here writes a
    valuation. It writes *evidence* — sales into ``market_sales``, listings into
    ``market_listings`` — and then asks the pricing engine to recompute from it.
    So a provider-supplied number and a user-supplied one go through exactly the
    same arithmetic, the same exclusion rules and the same outlier fence, and
    arrive carrying a sample size instead of standing on someone's authority.
    """
    caps = provider.capabilities()
    key = MarketKey(
        catalog_key=card.catalog_key,
        currency=currency,
        external_id=(card.external_ids or {}).get(source.code),
        variant=card.variant,
        query=CardQuery(
            name=card.name,
            set_code=card.set_code,
            set_name=card.set_name,
            card_number=card.card_number,
            variant=card.variant,
            language=card.language,
        ),
    )

    imported_any = False
    if caps.sales_history:
        imported_any = _import_sales(
            db, provider, card, key=key, source=source, currency=currency, rates=rates,
            outcome=outcome, report=report,
        )
    if caps.active_listings and outcome.status != "failed":
        _import_listings(
            db, provider, card, key=key, source=source, currency=currency, rates=rates,
            outcome=outcome, report=report,
        )

    if imported_any:
        # Re-fence and reprice from the sales that just landed. Without this the
        # rows exist and every number the user looks at is still the old one.
        sales_import.mark_outliers(db, card.catalog_key, params=params)
        market_service.recompute_key(db, card.catalog_key, params=params, currency=currency)
        outcome.grades = market_service.grade_labels_for(db, card.catalog_key)


def _import_sales(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    key: MarketKey,
    source: DataSource,
    currency: str,
    rates: dict,
    outcome: CardSyncOutcome,
    report: SyncReport,
) -> bool:
    try:
        records = provider.get_sales_history(key)
    except CapabilityDeniedError as exc:
        # Not a failure. The source is healthy and this part of it is not
        # granted to this account, which is a different sentence and a different
        # fix — and the rest of the run continues.
        _note_once(report, str(exc))
        outcome.reason = "Sold data is not granted to this application."
        return False
    except ProviderRequestError as exc:
        outcome.status = "failed"
        outcome.reason = str(exc)
        return False
    except Exception as exc:  # One bad adapter must not abort the whole run.
        outcome.status = "failed"
        outcome.reason = f"{source.name} adapter raised {type(exc).__name__}: {exc}"
        return False

    rows: list[sales_import.ImportRow] = []
    unconvertible: set[str] = set()
    for record in records:
        minor, _rate = convert_minor(
            record.price_minor, from_currency=record.currency, to_currency=currency, rates=rates
        )
        if minor is None:
            # Same refusal as the price path, for the same reason: a guessed
            # rate would rescale a whole sample and look entirely plausible.
            unconvertible.add(record.currency)
            continue
        shipping = None
        if record.shipping_minor is not None:
            shipping, _ = convert_minor(
                record.shipping_minor,
                from_currency=record.currency,
                to_currency=currency,
                rates=rates,
            )
        rows.append(
            sales_import.ImportRow(
                sale_date=record.sale_date,
                sale_price_minor=minor,
                shipping_minor=shipping,
                currency=currency,
                listing_title=record.listing_title,
                platform=record.platform,
                seller=record.seller,
                source_url=record.source_url,
                external_id=record.external_id,
                lot_size=record.lot_size,
                is_auction=record.is_auction,
            )
        )

    if unconvertible:
        outcome.source_currency = sorted(unconvertible)[0]
        outcome.reason = (
            f"{len(records) - len(rows)} sale(s) in {'/'.join(sorted(unconvertible))} were "
            f"fetched but not written: no rate to {currency} is set. Add one in Settings → "
            "Market and run this again."
        )

    if not rows:
        if not unconvertible:
            outcome.reason = outcome.reason or f"{source.name} has no recent sales for this card."
        return False

    # The card's own identity is what the exclusion rules compare a listing
    # title against — a Japanese copy or a reverse holo is not a comparable for
    # this card even when the name matches exactly.
    context = sales_import.SaleContext(
        catalog_key=card.catalog_key,
        language=card.language,
        variant=card.variant,
        printing=card.printing,
    )
    imported = sales_import.import_rows(
        db,
        rows,
        context=context,
        source_code=source.code,
        card_id=card.id,
        default_currency=currency,
    )

    outcome.status = "updated"
    outcome.sales_imported = imported.imported
    outcome.sales_updated = imported.updated
    outcome.sales_excluded = imported.excluded
    return bool(imported.total)


def _import_listings(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    key: MarketKey,
    source: DataSource,
    currency: str,
    rates: dict,
    outcome: CardSyncOutcome,
    report: SyncReport,
) -> None:
    """What is currently on sale. Asking prices, and never recorded as sales.

    They exist for one number: the sold-to-active ratio. Fifty copies listed and
    two sold a month is a different market from two listed and two sold, at
    identical prices, and only the second one is worth grading into.
    """
    try:
        records = provider.get_listings(key)
    except CapabilityDeniedError as exc:
        _note_once(report, str(exc))
        return
    except ProviderRequestError as exc:
        _note_once(report, f"Active listings could not be fetched: {exc}")
        return
    except Exception as exc:
        _note_once(report, f"{source.name} adapter raised {type(exc).__name__} on listings: {exc}")
        return

    # Everything previously seen from this source for this card is stale until
    # proved otherwise. Marking rather than deleting, so a listing that comes
    # back keeps its history, and so a failed fetch above leaves the table
    # untouched rather than emptied.
    for stale in db.scalars(
        select(MarketListing).where(
            MarketListing.catalog_key == card.catalog_key,
            MarketListing.source_id == source.id,
            MarketListing.is_active.is_(True),
        )
    ):
        stale.is_active = False

    seen = 0
    reported: int | None = None
    for record in records:
        minor, _rate = convert_minor(
            record.price_minor, from_currency=record.currency, to_currency=currency, rates=rates
        )
        if minor is None:
            continue
        reported = reported or (record.raw or {}).get("result_total")
        _write_listing(
            db, card=card, source=source, record=record, value_minor=minor, currency=currency
        )
        seen += 1

    outcome.listings_seen = seen
    # What the source says exists, which is the honest denominator. One page of
    # results is not the size of the market.
    outcome.listings_reported = reported if isinstance(reported, int) else None
    if outcome.status == "skipped" and seen:
        outcome.status = "updated"


def _write_listing(
    db: Session,
    *,
    card: Card,
    source: DataSource,
    record,
    value_minor: int,
    currency: str,
) -> None:
    row = None
    if record.external_id:
        row = db.scalars(
            select(MarketListing).where(
                MarketListing.source_id == source.id,
                MarketListing.external_id == record.external_id,
            )
        ).first()
    if row is None:
        row = MarketListing(catalog_key=card.catalog_key, price_minor=value_minor)
        db.add(row)

    parsed = sales_import.parse_grade_from_title(record.listing_title)
    row.catalog_key = card.catalog_key
    row.card_id = card.id
    row.grade_label = build_grade_label(*parsed) if parsed else "raw"
    row.grade = parsed[1] if parsed else None
    row.platform = record.platform
    row.listed_at = record.listed_at
    row.price_minor = value_minor
    row.currency = currency
    row.listing_title = record.listing_title
    row.source_url = record.source_url
    row.seller = record.seller
    row.is_auction = record.is_auction
    row.is_active = True
    row.source_id = source.id
    row.external_id = record.external_id
    row.raw_payload = record.raw or None
    row.seen_at = datetime.now(UTC)


def _note_once(report: SyncReport, message: str) -> None:
    """Per-run facts, said once however many cards hit them."""
    if message not in report.notes:
        report.notes.append(message)


def sync_cards(db: Session, *, card_ids: list[str] | None = None, limit: int = 200) -> list[SyncReport]:
    """Run every enabled price source, best priority first."""
    reports: list[SyncReport] = []
    for source in _price_sources(db):
        reports.append(sync_source(db, source, card_ids=card_ids, limit=limit))
    return reports


def _price_sources(db: Session) -> list[DataSource]:
    """Enabled sources that could actually return a price.

    Manual and CSV are enabled and are not network sources — asking them to
    sync would be asking the user's own data to refresh itself.
    """
    return [
        source
        for source in db.scalars(
            select(DataSource)
            .where(DataSource.enabled.is_(True), DataSource.provider_class.is_not(None))
            .order_by(DataSource.priority)
        )
        if source.code not in {"manual", "csv"}
    ]


def _cards_to_sync(
    db: Session,
    source: DataSource,
    provider: MarketDataProvider,
    *,
    card_ids: list[str] | None,
    limit: int,
) -> tuple[list[Card], int]:
    """Which cards this source could say anything about, and how many that was.

    A catalogue needs to have been told its own id for the card, so only linked
    cards qualify. A marketplace is searched by name, so every card qualifies —
    filtering to linked ones there would return nothing at all, forever, and
    look exactly like a working sync with an empty collection.

    Returns the capped list *and* the eligible total, so the caller can say when
    it did not get to everything.
    """
    stmt = select(Card).where(Card.catalog_key.is_not(None))
    if card_ids:
        stmt = stmt.where(Card.id.in_(card_ids))
    cards = list(db.scalars(stmt.order_by(Card.updated_at.desc())))

    if provider.requires_external_id:
        cards = [card for card in cards if (card.external_ids or {}).get(source.code)]
    return cards[:limit], len(cards)


def _summarise(report: SyncReport, *, currency: str, rates: dict) -> None:
    unconvertible = [row for row in report.cards if row.reason and "rate is set" in row.reason]
    if unconvertible:
        missing = sorted({row.source_currency for row in unconvertible if row.source_currency})
        report.notes.append(
            f"{len(unconvertible)} price(s) fetched but not written: no "
            f"{'/'.join(missing)}→{currency} rate is configured. Set one in Settings → Market."
        )

    unlinked = [row for row in report.cards if row.reason == "Not linked to this source yet."]
    if unlinked:
        report.notes.append(
            f"{len(unlinked)} card(s) are not linked to this source. Look each one up in the "
            "catalogue and confirm the match."
        )

    if report.failed:
        report.status = "partial" if report.updated else "error"
        report.reason = f"{report.failed} card(s) failed. The rest were left as they were."
    elif report.skipped and report.updated:
        report.status = "partial"
        report.reason = f"Updated {report.updated}, skipped {report.skipped}."
    elif not report.updated:
        report.status = "insufficient_data"
        report.reason = report.notes[0] if report.notes else "Nothing to update."
    if not rates and any(row.source_currency for row in report.cards):
        report.notes.append(
            "No exchange rates are configured at all, so no foreign-currency price can be "
            "written. This is deliberate: guessing a rate would rescale every price silently."
        )


def _abort(db: Session, source: DataSource, report: SyncReport, reason: str) -> SyncReport:
    report.status = "error"
    report.reason = reason
    _record(db, source, report)
    return report


def _record(db: Session, source: DataSource, report: SyncReport) -> None:
    """Write the outcome onto the source, so the UI can show it without a run."""
    report.finished_at = datetime.now(UTC).isoformat()
    source.last_sync_at = datetime.now(UTC)
    source.last_sync_status = report.status
    source.last_sync_error = report.reason if report.status in {"error", "partial"} else None
    db.flush()


def _display(card: Card) -> str:
    return f"{card.name} {card.card_number}".strip() if card.card_number else card.name
