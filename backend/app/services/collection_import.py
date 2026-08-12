"""Getting a whole collection in, from a file somebody else exported.

Until this existed the only way in was the Add Card form, one card at a time.
That is fine for the card in your hand and hopeless for the four hundred in a
box, which made it the last thing standing between the engine and a real
collection: an application that decides what to grade is worth nothing until it
knows what you own.

Three things shape it.

**Preview first.** A bad import is not a wrong answer on screen, it is four
hundred rows you now have to find and delete. So the default run parses,
matches, counts and writes nothing, and what you approve is what was actually
read rather than a promise about it.

**A coarse condition is a label, not an assessment.** Exports carry a
``Condition`` column holding "NM" or "Lightly Played", and spec section 6
rejects exactly that as a condition model. It lands in ``raw_condition``, which
is documented as a quick label and which no engine reads. It deliberately does
**not** become a ``condition_assessment``: inventing per-corner severities from
one word would put fabricated evidence under a grading decision, and a wide
guess presented as a measurement is worse than no measurement. Imported cards
therefore report "not assessed" until somebody looks at them, which is true.

**Re-importing the same file must not double the collection.** Rows are matched
against existing cards on ``catalog_key``, and a match is reported rather than
silently skipped or silently duplicated — you might genuinely have bought a
second copy, and only you know which it is.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import Language, Printing, RawCondition
from app.models import Card
from app.services import cards_service
from app.services.identity import build_catalog_key
from app.services.sales_import import RowError, parse_money

__all__ = [
    "COLUMN_ALIASES",
    "CollectionImportReport",
    "ImportedCard",
    "import_collection",
    "parse_collection_csv",
]

#: Column names understood, beyond the canonical name itself. Drawn from what
#: the common exporters actually emit — a file should import without being
#: rewritten first, because rewriting it by hand is the work this replaces.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "cardname", "card", "title", "productname", "product"),
    "set_name": ("setname", "set", "edition", "expansion", "setsname"),
    "set_code": ("setcode", "code", "abbreviation", "setabbreviation"),
    "card_number": ("cardnumber", "number", "no", "num", "collectornumber", "cardno"),
    # Foil and holo describe the *variant*, not the printing. Keeping them apart
    # matters more than it looks: both feed `catalog_key`, so putting "holofoil"
    # in the printing slot would give an imported card a different identity from
    # the same card added by hand, and the two would never share a price.
    "variant": ("variant", "arttype", "style", "foil", "holo", "isfoil", "finish"),
    "printing": ("printing", "printrun", "printingrun"),
    "language": ("language", "lang"),
    "rarity": ("rarity",),
    "quantity": ("quantity", "qty", "count", "copies", "amount"),
    "raw_condition": ("condition", "rawcondition", "grade", "cardcondition"),
    "purchase_price": ("purchaseprice", "price", "paid", "cost", "buyprice", "acquiredfor"),
    "purchase_currency": ("purchasecurrency", "currency", "ccy"),
    "purchase_date": ("purchasedate", "acquired", "dateacquired", "bought", "datebought"),
}

_HEADER_NOISE = re.compile(r"[^a-z0-9]")

#: How the coarse words map onto our own vocabulary. Everything unrecognised
#: stays ``Unknown`` rather than being rounded to the nearest guess.
CONDITION_WORDS: dict[str, str] = {
    "gemmint": RawCondition.GEM_MINT.value,
    "gm": RawCondition.GEM_MINT.value,
    "mint": RawCondition.MINT.value,
    "m": RawCondition.MINT.value,
    "nearmint": RawCondition.NEAR_MINT.value,
    "nm": RawCondition.NEAR_MINT.value,
    "nmmt": RawCondition.NEAR_MINT.value,
    "nearmintmint": RawCondition.NEAR_MINT.value,
    "excellent": RawCondition.LIGHTLY_PLAYED.value,
    "ex": RawCondition.LIGHTLY_PLAYED.value,
    "lightlyplayed": RawCondition.LIGHTLY_PLAYED.value,
    "lp": RawCondition.LIGHTLY_PLAYED.value,
    "good": RawCondition.MODERATELY_PLAYED.value,
    "moderatelyplayed": RawCondition.MODERATELY_PLAYED.value,
    "mp": RawCondition.MODERATELY_PLAYED.value,
    "played": RawCondition.MODERATELY_PLAYED.value,
    "pl": RawCondition.MODERATELY_PLAYED.value,
    "heavilyplayed": RawCondition.HEAVILY_PLAYED.value,
    "hp": RawCondition.HEAVILY_PLAYED.value,
    "poor": RawCondition.DAMAGED.value,
    "damaged": RawCondition.DAMAGED.value,
    "dmg": RawCondition.DAMAGED.value,
}

#: What a "Foil" column says when it means yes, and what we call that.
_FOIL_WORDS = {"1", "true", "yes", "y", "foil", "holo", "holofoil", "holographic"}
_PLAIN_WORDS = {"0", "false", "no", "n", "normal", "nonfoil", "regular", "none"}


@dataclass
class ImportedCard:
    """One row, parsed and matched, before anything is written."""

    line_number: int
    name: str
    set_name: str | None = None
    set_code: str | None = None
    card_number: str | None = None
    variant: str | None = None
    printing: str | None = None
    language: str = Language.ENGLISH.value
    rarity: str | None = None
    quantity: int = 1
    raw_condition: str = RawCondition.UNKNOWN.value
    purchase_price_minor: int | None = None
    purchase_currency: str | None = None
    purchase_date: date | None = None
    catalog_key: str | None = None
    #: Set when this row matches a card already in the collection.
    duplicate_of: str | None = None
    #: What the file said, when we could not make sense of it.
    condition_as_written: str | None = None


@dataclass
class CollectionImportReport:
    dry_run: bool = True
    status: str = "ok"
    reason: str | None = None
    #: Rows that would be, or were, added.
    imported: int = 0
    #: Rows matching a card already held.
    duplicates: int = 0
    #: Rows that could not be read at all.
    failed: int = 0
    cards: list[ImportedCard] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _canonical_header(header: str) -> str | None:
    key = _HEADER_NOISE.sub("", header.lower())
    for canonical, aliases in COLUMN_ALIASES.items():
        if key == _HEADER_NOISE.sub("", canonical) or key in aliases:
            return canonical
    return None


def parse_condition(value: str | None) -> tuple[str, str | None]:
    """Map a coarse word onto our vocabulary, keeping what we could not read.

    Returns ``(raw_condition, unrecognised_text)``. Anything unfamiliar stays
    ``Unknown`` and travels back as written, because guessing that "MINT-ISH"
    means Mint is the kind of quiet reinterpretation that ends up under a
    grading decision.
    """
    if not value or not value.strip():
        return RawCondition.UNKNOWN.value, None
    key = _HEADER_NOISE.sub("", value.lower())
    if key in CONDITION_WORDS:
        return CONDITION_WORDS[key], None
    return RawCondition.UNKNOWN.value, value.strip()


def _parse_quantity(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return 1
    digits = re.sub(r"[^0-9]", "", str(value))
    if not digits:
        return None
    count = int(digits)
    return count if 1 <= count <= 10_000 else None


def _parse_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return date.fromisoformat(text) if pattern == "%Y-%m-%d" else _strptime(text, pattern)
        except ValueError:
            continue
    return None


def _strptime(text: str, pattern: str) -> date:
    from datetime import datetime

    return datetime.strptime(text, pattern).date()


def _match_enum(value: str | None, options: type[StrEnum]) -> str | None:
    """Case-insensitively map a word onto one of our own values.

    Returns ``None`` when nothing matches, so the caller decides between keeping
    the text and dropping it. Both of those beat writing an unrecognised word
    into a column that feeds ``catalog_key``.
    """
    if not value or not value.strip():
        return None
    key = _HEADER_NOISE.sub("", value.lower())
    for option in options:
        if _HEADER_NOISE.sub("", option.value.lower()) == key:
            return option.value
    return None


def foil_variant(value: str | None) -> str | None:
    """The variant a foiling word names, or ``None`` if it is not one."""
    if not value or not value.strip():
        return None
    key = _HEADER_NOISE.sub("", value.lower())
    if key in {"reverseholo", "reverse", "rh"}:
        return "Reverse Holo"
    if key in _FOIL_WORDS:
        return "Holo"
    if key in _PLAIN_WORDS:
        return "Standard"
    return None


def parse_variant(value: str | None) -> str | None:
    """A "Foil" column is a variant, not a printing.

    ``Holo`` and ``Reverse Holo`` are variant rows; ``printing`` is Unlimited /
    1st Edition / Shadowless. Both go into ``catalog_key``, so filing one under
    the other gives an imported card an identity no hand-added card will ever
    match, and the two copies never share a price.
    """
    if not value or not value.strip():
        return None
    # Anything we do not recognise as foiling is passed through: the export may
    # well have written a real variant name, and `resolve_references` matches
    # those by name.
    return foil_variant(value) or value.strip()


def parse_printing(value: str | None) -> str | None:
    """Unlimited, 1st Edition, Shadowless — and nothing else, silently."""
    if not value or not value.strip():
        return None
    key = _HEADER_NOISE.sub("", value.lower())
    if key in {"1st", "first", "firsted", "1sted"}:
        return Printing.FIRST_EDITION.value
    return _match_enum(value, Printing)


def parse_collection_csv(text: str) -> tuple[list[ImportedCard], list[RowError]]:
    """Read a CSV into rows, collecting per-line errors rather than raising.

    A file with three unreadable lines out of four hundred should import three
    hundred and ninety-seven and name the three.
    """
    stripped = text.lstrip("﻿")
    if not stripped.strip():
        return [], [RowError(None, "The file is empty.")]

    try:
        dialect = csv.Sniffer().sniff(stripped[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(stripped), dialect)

    try:
        headers = next(reader)
    except StopIteration:  # pragma: no cover - guarded by the empty check
        return [], [RowError(None, "The file is empty.")]

    mapping = {index: _canonical_header(name) for index, name in enumerate(headers)}
    if "name" not in mapping.values():
        return [], [
            RowError(
                1,
                "No card-name column found. A name is the one thing a row cannot do without; "
                f"understood names include: {', '.join(sorted(COLUMN_ALIASES))}.",
            )
        ]

    rows: list[ImportedCard] = []
    errors: list[RowError] = []
    for line_number, raw in enumerate(reader, start=2):
        if not any(cell.strip() for cell in raw):
            continue
        values: dict[str, str] = {}
        for index, cell in enumerate(raw):
            key = mapping.get(index)
            if key and cell.strip():
                values[key] = cell.strip()

        name = values.get("name")
        if not name:
            errors.append(RowError(line_number, "No card name on this row."))
            continue

        quantity = _parse_quantity(values.get("quantity"))
        if quantity is None:
            errors.append(
                RowError(line_number, f"Could not read the quantity {values.get('quantity')!r}.")
            )
            continue

        condition, as_written = parse_condition(values.get("raw_condition"))

        variant = parse_variant(values.get("variant"))
        printing = parse_printing(values.get("printing"))
        if printing is None and values.get("printing"):
            # Exporters disagree about which header carries foiling: one writes
            # "Foil: Yes", another "Printing: Holofoil". Rather than drop a fact
            # the user gave us, a foiling word in the printing column is read as
            # what it plainly is.
            printing_as_variant = foil_variant(values["printing"])
            if printing_as_variant and variant is None:
                variant = printing_as_variant

        written_language = values.get("language")
        # Kept as written when we do not recognise it, rather than silently
        # called English: a Japanese copy priced against English sales is a
        # wrong number, not a typo. The report names them.
        language = _match_enum(written_language, Language) or written_language

        rows.append(
            ImportedCard(
                line_number=line_number,
                name=name[:160],
                set_name=values.get("set_name"),
                set_code=values.get("set_code"),
                card_number=values.get("card_number"),
                variant=variant,
                printing=printing,
                language=language or Language.ENGLISH.value,
                rarity=values.get("rarity"),
                quantity=quantity,
                raw_condition=condition,
                condition_as_written=as_written,
                purchase_price_minor=parse_money(values.get("purchase_price")),
                purchase_currency=(values.get("purchase_currency") or None),
                purchase_date=_parse_date(values.get("purchase_date")),
            )
        )
    return rows, errors


def import_collection(
    db: Session,
    csv_text: str,
    *,
    dry_run: bool = True,
    skip_duplicates: bool = True,
) -> CollectionImportReport:
    """Parse a collection export and, unless this is a dry run, add the cards.

    ``dry_run`` is the default. Four hundred unwanted rows are far harder to
    undo than they were to create, so the first thing this can do is read the
    file, say exactly what it found, and change nothing.
    """
    report = CollectionImportReport(dry_run=dry_run)
    rows, errors = parse_collection_csv(csv_text)
    report.errors = errors
    report.failed = len(errors)

    if not rows:
        report.status = "error" if errors else "insufficient_data"
        report.reason = (
            errors[0].message if errors else "The file had a header but no rows under it."
        )
        return report

    held = {
        key
        for key in db.scalars(select(Card.catalog_key).where(Card.catalog_key.is_not(None)))
        if key
    }

    for row in rows:
        # Built through the same path a saved card takes, and only then read
        # back. `resolve_references` fills a known set's code in from its name
        # and re-derives `catalog_key` from it, so a key computed off the raw
        # row would be the key of a card that never existed — and the re-import
        # it is supposed to recognise would sail straight past it.
        card = Card()
        card.name = row.name
        card.set_name = row.set_name
        card.set_code = row.set_code
        card.card_number = row.card_number
        card.variant = row.variant
        card.printing = row.printing
        card.language = row.language
        card.rarity = row.rarity
        card.quantity = row.quantity
        card.raw_condition = row.raw_condition
        card.purchase_price_minor = row.purchase_price_minor
        card.purchase_currency = row.purchase_currency
        card.purchase_date = row.purchase_date
        cards_service.resolve_references(db, card)

        # A card entered with a set *name* and no code keys off the name, and
        # one entered with the code keys off the code. Resolving the reference
        # now closes that gap for anything created from here on, but cards
        # already in the collection may carry either — so a row is matched
        # against both spellings of its own identity. Checking only the resolved
        # one would quietly duplicate every card added before this existed,
        # which is the precise failure the duplicate check is here to prevent.
        unresolved_key = build_catalog_key(
            name=row.name,
            set_code=row.set_code,
            set_name=row.set_name,
            card_number=row.card_number,
            variant=row.variant,
            language=row.language,
            printing=row.printing,
        )

        # Show what the card will actually hold, not what the file said.
        row.set_code = card.set_code
        row.set_name = card.set_name
        row.variant = card.variant
        row.catalog_key = card.catalog_key

        already = next((key for key in (card.catalog_key, unresolved_key) if key in held), None)
        if already:
            row.duplicate_of = already
            report.duplicates += 1
            if skip_duplicates:
                continue
        report.imported += 1
        if not dry_run:
            db.add(card)
        # A second copy in the same file is a second card, so the key counts as
        # held from here on — otherwise a file listing one card twice reports
        # two fresh imports and quietly creates a duplicate. True of a dry run
        # too, or the preview would promise more than the commit delivers.
        held.add(row.catalog_key)

    report.cards = rows
    _summarise(report)
    return report


def _summarise(report: CollectionImportReport) -> None:
    if not report.imported:
        report.status = "insufficient_data"
        report.reason = (
            f"Every row matches a card you already hold ({report.duplicates})."
            if report.duplicates
            else "Nothing in the file could be read as a card."
        )
    elif report.failed:
        report.status = "partial"
        report.reason = f"{report.failed} row(s) could not be read; the rest are fine."

    if report.duplicates:
        report.notes.append(
            f"{report.duplicates} row(s) match a card already in your collection. They are "
            "skipped by default — turn that off if you really did buy a second copy, since "
            "the engine decides per physical card."
        )
    known = {option.value for option in Language}
    strange = sorted({row.language for row in report.cards if row.language not in known})
    if strange:
        report.notes.append(
            f"Unrecognised language(s): {', '.join(strange)}. Kept as written rather than "
            "assumed to be English — language is part of a card's identity, so getting it "
            "wrong prices the card against the wrong sales."
        )

    unreadable = [row.condition_as_written for row in report.cards if row.condition_as_written]
    if unreadable:
        report.notes.append(
            f"{len(unreadable)} row(s) had a condition we do not recognise "
            f"(e.g. {unreadable[0]!r}), so those cards import as Unknown rather than as a guess."
        )
    report.notes.append(
        "A condition column is stored as a label only. It is not a condition assessment and no "
        "engine reads it, so imported cards stay undecided until you assess them — which is the "
        "honest position, since one word from a spreadsheet is not a look at the card."
    )
