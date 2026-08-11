"""Getting comparable sales in, and keeping the bad ones out (spec section 15).

A valuation is only as honest as the sales behind it. The single fastest way to
produce a confidently wrong number is to average a job lot of 200 cards, a
creased copy, a Japanese print and a graded slab into one "raw" median. So every
sale that lands here is classified before it is trusted.

Three rules govern the filtering:

**Excluded is not deleted.** An excluded sale keeps its row, its reason and who
excluded it (``system`` or ``user``). Every automatic decision is visible and
reversible, because these are heuristics reading listing titles and heuristics
are wrong sometimes.

**Only positive evidence excludes.** A title that says "Japanese" is evidence.
A title that fails to say "English" is not. Silence never triggers an exclusion,
which keeps the filter from quietly deleting most of a small sample.

**Outliers need a sample.** The IQR fence is off below
``min_sales_for_outliers``: with five sales the fence is drawn by the very
points it is meant to judge.

Import itself is deduplicated on ``(source_id, external_id)``, so re-importing
last month's CSV updates rather than doubles.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import SaleExclusionReason
from app.models import DataSource, GradingCompany, MarketListing, MarketSale, PriceSnapshot
from app.money import to_minor
from app.services import market_service
from app.services.identity import grade_label as build_grade_label

__all__ = [
    "ImportReport",
    "ImportRow",
    "RowError",
    "SaleContext",
    "classify",
    "import_rows",
    "mark_outliers",
    "migrate_card_key",
    "parse_csv",
    "parse_grade_from_title",
    "parse_money",
    "reclassify_key",
    "set_exclusion",
]


# --- Exclusion heuristics ----------------------------------------------------


@dataclass(frozen=True)
class TitleRule:
    """One heuristic over a listing title.

    ``label`` is shown to the user next to the exclusion, so it has to say what
    was actually matched — "mentions a lot or bundle", not "rule 4".
    """

    reason: str
    label: str
    pattern: re.Pattern[str]


def _rule(reason: SaleExclusionReason, label: str, *alternatives: str) -> TitleRule:
    joined = "|".join(alternatives)
    return TitleRule(reason.value, label, re.compile(joined, re.IGNORECASE))


# Deliberately conservative. A false exclusion costs a real comparable and is
# harder to notice than a false inclusion, which shows up as an obvious outlier.
TITLE_RULES: tuple[TitleRule, ...] = (
    _rule(
        SaleExclusionReason.LOT_OR_BUNDLE,
        "mentions a lot, bundle or multiple cards",
        r"\bjob[ -]?lots?\b",
        r"\blots?\s+of\b",
        r"\bbundles?\b",
        r"\bbulk\b",
        r"\bmystery\b",
        r"\brandom\b",
        r"\bwholesale\b",
        r"\b\d{2,}\s*(?:x\s*)?cards?\b",
        r"\bx\s?\d{2,}\b",
        r"\bset\s+of\s+\d+\b",
        r"\bplaysets?\b",
        r"\bmaster\s+set\b",
        r"\bcomplete\s+set\b",
        r"\bbooster\s+box\b",
        r"\betb\b",
        r"\belite\s+trainer\b",
        r"\bsealed\b",
    ),
    _rule(
        SaleExclusionReason.DAMAGED,
        "describes damage or heavy play",
        r"\bdamaged?\b",
        r"\bdmg\b",
        r"\bcreased?\b",
        r"\bcreasing\b",
        r"\bbent\b",
        r"\btorn\b",
        r"\bripped\b",
        r"\bwater\s?damage",
        r"\bheavily\s+played\b",
        r"\bpoor\s+condition\b",
        r"\bplayed\s+condition\b",
        r"\bfor\s+parts\b",
        r"\bas\s+is\b",
    ),
    _rule(
        SaleExclusionReason.SUSPECTED_FAKE,
        "looks like a custom, proxy or counterfeit",
        r"\bfakes?\b",
        r"\bproxy\b",
        r"\bproxies\b",
        r"\breplica\b",
        r"\bcounterfeit\b",
        r"\bcustom\b",
        r"\borica\b",
        r"\bfan\s?made\b",
        r"\bnot\s+official\b",
        r"\bmetal\s+card\b",
    ),
    _rule(
        SaleExclusionReason.BEST_OFFER_UNKNOWN,
        "sold via best offer, so the price shown is not the price paid",
        r"\bbest\s+offer\b",
        r"\bobo\b",
    ),
)

# Language names as they appear in listing titles. Only used to spot a *mismatch*
# against the identity being valued.
LANGUAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("japanese", re.compile(r"\b(?:japanese|japan|jpn|jp)\b", re.IGNORECASE)),
    ("korean", re.compile(r"\b(?:korean|korea|kor)\b", re.IGNORECASE)),
    ("chinese", re.compile(r"\b(?:chinese|china|s-chinese|t-chinese)\b", re.IGNORECASE)),
    ("german", re.compile(r"\b(?:german|deutsch)\b", re.IGNORECASE)),
    ("french", re.compile(r"\b(?:french|francais|français)\b", re.IGNORECASE)),
    ("spanish", re.compile(r"\b(?:spanish|espanol|español)\b", re.IGNORECASE)),
    ("italian", re.compile(r"\b(?:italian|italiano)\b", re.IGNORECASE)),
    ("portuguese", re.compile(r"\b(?:portuguese|portugues|português)\b", re.IGNORECASE)),
    ("russian", re.compile(r"\brussian\b", re.IGNORECASE)),
    ("thai", re.compile(r"\bthai\b", re.IGNORECASE)),
    ("indonesian", re.compile(r"\bindonesian\b", re.IGNORECASE)),
    ("english", re.compile(r"\benglish\b", re.IGNORECASE)),
)

# Variant mismatches worth catching. A reverse holo is a different market from a
# regular holo and trades at a different price, so blending them is exactly the
# error ``catalog_key`` exists to prevent.
VARIANT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("reverse-holo", re.compile(r"\breverse\s*(?:holo|foil)?\b|\brev\s+holo\b", re.IGNORECASE)),
    ("alternate-art", re.compile(r"\balt(?:ernate)?\s*art\b|\balt\b", re.IGNORECASE)),
    ("full-art", re.compile(r"\bfull\s*art\b|\bfa\b", re.IGNORECASE)),
    ("secret-rare", re.compile(r"\bsecret\s*rare\b", re.IGNORECASE)),
    ("rainbow-rare", re.compile(r"\brainbow\b", re.IGNORECASE)),
    ("gold", re.compile(r"\bgold\s+(?:card|rare|secret)\b", re.IGNORECASE)),
    ("promo", re.compile(r"\bpromo\b", re.IGNORECASE)),
    ("first-edition", re.compile(r"\b1st\s*ed(?:ition)?\b|\bfirst\s+edition\b", re.IGNORECASE)),
    ("shadowless", re.compile(r"\bshadowless\b", re.IGNORECASE)),
)

GRADE_IN_TITLE = re.compile(
    r"\b(psa|cgc|bgs|beckett|sgc|ace|tag|gma|hga)\s*[-:]?\s*(10|[1-9](?:\.5)?)\b",
    re.IGNORECASE,
)
# "Ungraded"/"raw" said explicitly — a positive claim that this is not a slab.
RAW_IN_TITLE = re.compile(r"\b(?:raw|ungraded|un-graded)\b", re.IGNORECASE)


@dataclass
class SaleContext:
    """What the sale is *supposed* to be, so a mismatch can be spotted.

    Everything is optional: with nothing to compare against, the language,
    variant and grade checks simply do not fire.
    """

    catalog_key: str
    language: str | None = None
    variant: str | None = None
    printing: str | None = None
    expected_grade_label: str | None = None


def _normalise(value: str | None) -> str:
    return (value or "").strip().lower()


def _language_mismatch(title: str, expected: str | None) -> bool:
    want = _normalise(expected)
    if not want:
        return False
    found = [name for name, pattern in LANGUAGE_PATTERNS if pattern.search(title)]
    if not found:
        return False
    # Titles often list several languages ("English / Japanese available"), and
    # an ambiguous title is not evidence of the wrong one.
    return want not in found


_GENERIC_VARIANTS = {"", "standard", "normal", "regular", "unlimited", "unknown"}

# What the user types, mapped to the pattern names above. "1st Edition" and
# "First Edition" are the same market; the filter has to know that before it
# excludes every genuine comparable a 1st edition card has.
VARIANT_ALIASES: dict[str, str] = {
    "1st-edition": "first-edition",
    "1st-ed": "first-edition",
    "first-ed": "first-edition",
    "reverse": "reverse-holo",
    "reverse-foil": "reverse-holo",
    "alt-art": "alternate-art",
    "alt": "alternate-art",
    "fa": "full-art",
    "secret": "secret-rare",
    "rainbow": "rainbow-rare",
    "gold-secret": "gold",
}


def _variant_token(value: str | None) -> str:
    slug = _normalise(value).replace(" ", "-")
    return VARIANT_ALIASES.get(slug, slug)


def _variant_mismatch(title: str, expected: str | None, printing: str | None = None) -> bool:
    """Does the title name a variant this card is not?

    ``printing`` is accepted alongside ``variant`` because "1st Edition" lives in
    one field or the other depending on the card, and a 1st edition comparable
    for a 1st edition card is not a mismatch.
    """
    accepted = {_variant_token(value) for value in (expected, printing)}
    accepted = {value for value in accepted if value}
    want = _variant_token(expected)
    if not want:
        return False
    found = [name for name, pattern in VARIANT_PATTERNS if pattern.search(title)]
    if not found or accepted & set(found):
        return False
    # "standard" is the absence of a variant, so any positively identified
    # variant is a mismatch. For a named variant, only exclude when the title
    # names exactly one *other* variant — multi-variant titles are ambiguous.
    if want in _GENERIC_VARIANTS:
        return True
    return len(found) == 1


def parse_grade_from_title(title: str | None) -> tuple[str, float] | None:
    """Pull ``("PSA", 10.0)`` out of a listing title, if it names a grade."""
    if not title:
        return None
    match = GRADE_IN_TITLE.search(title)
    if match is None:
        return None
    company = match.group(1).upper()
    if company == "BECKETT":
        company = "BGS"
    try:
        return company, float(match.group(2))
    except ValueError:  # pragma: no cover - the pattern only matches numbers
        return None


def _grade_mismatch(title: str, expected_label: str | None) -> bool:
    want = _normalise(expected_label)
    if not want:
        return False
    parsed = parse_grade_from_title(title)
    if parsed is None:
        # A title that says "raw"/"ungraded" while we are valuing a slab is a
        # mismatch; saying nothing at all is not.
        return bool(RAW_IN_TITLE.search(title)) and want != "raw"
    company, grade = parsed
    return build_grade_label(company, grade).lower() != want


def classify(
    *,
    title: str | None,
    context: SaleContext,
    lot_size: int = 1,
    grade_label: str | None = None,
) -> tuple[str, str] | None:
    """Decide whether a sale should be excluded, and say why.

    Returns ``(reason, human explanation)`` or ``None`` to keep the sale. The
    first matching rule wins, so the order of ``TITLE_RULES`` is the order the
    user sees reasons in.
    """
    if lot_size > 1:
        return (
            SaleExclusionReason.LOT_OR_BUNDLE.value,
            f"Listed as {lot_size} cards, so the price is not for one card.",
        )

    text = title or ""
    if text.strip():
        for rule in TITLE_RULES:
            match = rule.pattern.search(text)
            if match is not None:
                return rule.reason, f"Title {rule.label} (matched “{match.group(0)}”)."

        if _language_mismatch(text, context.language):
            return (
                SaleExclusionReason.WRONG_LANGUAGE.value,
                f"Title names a different language; this card is {context.language}.",
            )
        if _variant_mismatch(text, context.variant, context.printing):
            return (
                SaleExclusionReason.WRONG_VARIANT.value,
                f"Title names a different variant; this card is {context.variant or 'standard'}.",
            )
        wanted_label = grade_label or context.expected_grade_label
        if _grade_mismatch(text, wanted_label):
            return (
                SaleExclusionReason.WRONG_GRADE.value,
                f"Title names a different grade; this comparison is for {wanted_label}.",
            )
    return None


# --- Parsing raw input -------------------------------------------------------

_CURRENCY_SYMBOLS = re.compile(r"[£$€¥]|\b(?:gbp|usd|eur|cad|aud|jpy)\b", re.IGNORECASE)
_NOT_NUMERIC = re.compile(r"[^0-9.,\-]")


def parse_money(value: str | float | int | None) -> int | None:
    """Parse a price into minor units, tolerating however it was exported.

    Handles ``£1,234.56``, ``1234.56``, ``1.234,56`` and ``45``. When a string
    contains both separators the *last* one is the decimal point; a lone comma
    followed by exactly two digits is treated as a decimal comma.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return to_minor(value)

    text = _CURRENCY_SYMBOLS.sub("", str(value)).strip()
    text = _NOT_NUMERIC.sub("", text)
    if not text or text in {"-", ".", ","}:
        return None

    last_dot = text.rfind(".")
    last_comma = text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        comma_is_decimal = last_comma > last_dot
        text = text.replace(".", "").replace(",", ".") if comma_is_decimal else text.replace(",", "")
    elif last_comma >= 0:
        decimals = len(text) - last_comma - 1
        text = text.replace(",", "." if decimals == 2 else "")

    try:
        return to_minor(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


_DATE_FORMATS_DAY_FIRST = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y")
_DATE_FORMATS_MONTH_FIRST = ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y")
_DATE_FORMATS_UNAMBIGUOUS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d-%b-%Y",
)


def parse_date(value: str | date | datetime | None, *, day_first: bool = True) -> date | None:
    """Parse a sale date.

    ``day_first`` decides ``03/04/2025``. It defaults to day-first because the
    application's default currency is GBP, and it is exposed on the import call
    rather than guessed per row — a file is written one way throughout.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[,]", "", text)
    # Trim a trailing time component ("2025-03-04 14:22:01", "2025-03-04T14:22Z").
    text = re.split(r"[T ]\d{1,2}:", text)[0].strip()

    ordered = _DATE_FORMATS_DAY_FIRST if day_first else _DATE_FORMATS_MONTH_FIRST
    other = _DATE_FORMATS_MONTH_FIRST if day_first else _DATE_FORMATS_DAY_FIRST
    for fmt in (*_DATE_FORMATS_UNAMBIGUOUS, *ordered, *other):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --- CSV ---------------------------------------------------------------------

# Header aliases, lowercased and stripped of punctuation. Exports name these
# fields a dozen different ways and asking the user to rename columns before
# importing is a good way to make them not import anything.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "sale_date": ("saledate", "date", "solddate", "datesold", "enddate", "endedon", "soldon"),
    "sale_price": ("saleprice", "price", "soldprice", "soldfor", "amount", "total", "pricesold"),
    "shipping": ("shipping", "postage", "shippingcost", "delivery", "deliverycost", "pandp"),
    "currency": ("currency", "ccy"),
    "listing_title": ("listingtitle", "title", "item", "itemtitle", "name", "description"),
    "platform": ("platform", "site", "marketplace", "venue"),
    "grade_label": ("gradelabel", "slab"),
    "grade": ("grade", "gradevalue"),
    "company": ("company", "grader", "gradingcompany", "gradingco", "gradedby"),
    "seller": ("seller", "sellername", "vendor"),
    "source_url": ("sourceurl", "url", "link", "itemurl", "listingurl"),
    "external_id": ("externalid", "id", "itemid", "listingid", "orderid", "itemnumber"),
    "lot_size": ("lotsize", "quantity", "qty", "count"),
    "bid_count": ("bidcount", "bids", "numberofbids"),
    "condition_note": ("conditionnote", "condition", "conditiondescription"),
    "is_auction": ("isauction", "auction", "format", "listingtype", "buyingformat"),
}

_HEADER_NOISE = re.compile(r"[^a-z0-9]")


def _canonical_header(header: str) -> str | None:
    key = _HEADER_NOISE.sub("", header.lower())
    for canonical, aliases in COLUMN_ALIASES.items():
        if key == _HEADER_NOISE.sub("", canonical) or key in aliases:
            return canonical
    return None


@dataclass
class ImportRow:
    """One parsed sale, before it reaches the database."""

    sale_date: date | None = None
    sale_price_minor: int | None = None
    shipping_minor: int | None = None
    currency: str | None = None
    listing_title: str | None = None
    platform: str | None = None
    grade_label: str | None = None
    grade: float | None = None
    company_code: str | None = None
    seller: str | None = None
    source_url: str | None = None
    external_id: str | None = None
    lot_size: int = 1
    bid_count: int | None = None
    condition_note: str | None = None
    is_auction: bool | None = None
    line_number: int | None = None


@dataclass
class RowError:
    line_number: int | None
    message: str
    values: dict[str, str] = field(default_factory=dict)


_TRUE_WORDS = {"1", "true", "yes", "y", "auction", "chinese auction"}
_FALSE_WORDS = {"0", "false", "no", "n", "fixed price", "buy it now", "bin", "fixedprice"}


def _parse_bool(value: str | None) -> bool | None:
    text = _normalise(value)
    if not text:
        return None
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return None


def _parse_int(value: str | None) -> int | None:
    text = _NOT_NUMERIC.sub("", str(value or "")).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_csv(text: str, *, day_first: bool = True) -> tuple[list[ImportRow], list[RowError]]:
    """Parse a CSV export into rows, collecting per-line errors rather than raising.

    A file with three bad lines out of two hundred should import a hundred and
    ninety-seven and tell the user about the three.
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
    except StopIteration:  # pragma: no cover - guarded by the empty check above
        return [], [RowError(None, "The file is empty.")]

    mapping = {index: _canonical_header(name) for index, name in enumerate(headers)}
    if not any(mapping.values()):
        return [], [
            RowError(
                1,
                "No recognised columns. A sale date and a price are the minimum; "
                f"understood names include: {', '.join(sorted(COLUMN_ALIASES))}.",
            )
        ]
    if "sale_date" not in mapping.values() or "sale_price" not in mapping.values():
        missing = [
            name
            for name in ("sale_date", "sale_price")
            if name not in mapping.values()
        ]
        return [], [RowError(1, f"Missing required column(s): {', '.join(missing)}.")]

    rows: list[ImportRow] = []
    errors: list[RowError] = []
    for line_number, raw in enumerate(reader, start=2):
        if not any(cell.strip() for cell in raw):
            continue
        values = {
            canonical: raw[index].strip()
            for index, canonical in mapping.items()
            if canonical is not None and index < len(raw)
        }
        row = ImportRow(line_number=line_number)
        row.sale_date = parse_date(values.get("sale_date"), day_first=day_first)
        row.sale_price_minor = parse_money(values.get("sale_price"))
        row.shipping_minor = parse_money(values.get("shipping"))
        row.currency = (values.get("currency") or "").strip().upper() or None
        row.listing_title = values.get("listing_title") or None
        row.platform = values.get("platform") or None
        row.grade_label = values.get("grade_label") or None
        row.company_code = (values.get("company") or "").strip().upper() or None
        row.seller = values.get("seller") or None
        row.source_url = values.get("source_url") or None
        row.external_id = values.get("external_id") or None
        row.bid_count = _parse_int(values.get("bid_count"))
        row.condition_note = values.get("condition_note") or None
        row.is_auction = _parse_bool(values.get("is_auction"))
        row.lot_size = _parse_int(values.get("lot_size")) or 1

        grade_text = values.get("grade")
        if grade_text:
            try:
                row.grade = float(_NOT_NUMERIC.sub("", grade_text) or "nan")
            except ValueError:
                row.grade = None
            if row.grade is not None and row.grade != row.grade:  # NaN
                row.grade = None

        problems = []
        if row.sale_date is None:
            problems.append("could not read the sale date")
        if row.sale_price_minor is None:
            problems.append("could not read the price")
        elif row.sale_price_minor <= 0:
            problems.append("the price is zero or negative")
        if problems:
            errors.append(RowError(line_number, " and ".join(problems).capitalize() + ".", values))
            continue
        rows.append(row)

    return rows, errors


# --- Import ------------------------------------------------------------------


@dataclass
class ImportReport:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    excluded: int = 0
    errors: list[RowError] = field(default_factory=list)
    sale_ids: list[str] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.imported + self.updated


def _company_lookup(db: Session) -> dict[str, GradingCompany]:
    return {company.code.upper(): company for company in db.scalars(select(GradingCompany))}


def _resolve_grade(
    row: ImportRow, companies: dict[str, GradingCompany]
) -> tuple[str, float | None, str | None]:
    """Work out ``(grade_label, grade, company_id)`` for a row.

    An explicit company + grade wins. Failing that, a grade named in the title
    is used — an eBay export usually has the grade nowhere else.
    """
    code = row.company_code
    grade = row.grade
    if row.grade_label and not (code and grade is not None):
        parsed = parse_grade_from_title(row.grade_label)
        if parsed is not None:
            code, grade = parsed[0], parsed[1]
        elif _normalise(row.grade_label) in {"raw", "ungraded", "none", "-"}:
            return "raw", None, None
    if not (code and grade is not None):
        parsed = parse_grade_from_title(row.listing_title)
        if parsed is not None:
            code, grade = parsed[0], parsed[1]

    if not code or grade is None:
        return "raw", None, None
    company = companies.get(code.upper())
    return build_grade_label(code, grade), grade, (company.id if company else None)


def import_rows(
    db: Session,
    rows: list[ImportRow],
    *,
    context: SaleContext,
    source_code: str = "csv",
    card_id: str | None = None,
    default_currency: str = "GBP",
    apply_filters: bool = True,
) -> ImportReport:
    """Write parsed rows against one identity, deduplicated and classified.

    Dedupe is on ``(source_id, external_id)``: importing the same export twice
    updates the existing rows instead of doubling the sample. Rows with no
    external id cannot be deduplicated and are always inserted, which is why
    manual entry hands one in.
    """
    report = ImportReport()
    source = db.scalar(select(DataSource).where(DataSource.code == source_code))
    companies = _company_lookup(db)

    for row in rows:
        if row.sale_date is None or row.sale_price_minor is None:
            report.skipped += 1
            continue

        label, grade, company_id = _resolve_grade(row, companies)
        existing = None
        if source is not None and row.external_id:
            existing = db.scalar(
                select(MarketSale).where(
                    MarketSale.source_id == source.id,
                    MarketSale.external_id == row.external_id,
                )
            )

        sale = existing or MarketSale(catalog_key=context.catalog_key)
        sale.catalog_key = context.catalog_key
        sale.card_id = card_id
        sale.company_id = company_id
        sale.grade = grade
        sale.grade_label = label
        sale.platform = row.platform
        sale.sale_date = row.sale_date
        sale.sale_price_minor = row.sale_price_minor
        sale.currency = row.currency or default_currency
        sale.shipping_minor = row.shipping_minor
        sale.condition_note = row.condition_note
        sale.listing_title = row.listing_title
        sale.source_url = row.source_url
        sale.seller = row.seller
        sale.bid_count = row.bid_count
        sale.lot_size = max(1, row.lot_size)
        sale.is_auction = row.is_auction
        sale.source_id = source.id if source else None
        sale.external_id = row.external_id

        # A user exclusion is a decision, and re-importing the file should not
        # overwrite it.
        if apply_filters and sale.excluded_by != "user":
            verdict = classify(
                title=row.listing_title,
                context=context,
                lot_size=sale.lot_size,
                grade_label=label,
            )
            if verdict is None:
                sale.is_excluded = False
                sale.exclusion_reason = None
                sale.excluded_by = None
            else:
                reason, _explanation = verdict
                sale.is_excluded = True
                sale.exclusion_reason = reason
                sale.excluded_by = "system"
                report.excluded += 1
                report.exclusions[reason] = report.exclusions.get(reason, 0) + 1

        if existing is None:
            db.add(sale)
            report.imported += 1
        else:
            report.updated += 1
        db.flush()
        report.sale_ids.append(sale.id)

    return report


def set_exclusion(
    db: Session,
    sale: MarketSale,
    *,
    excluded: bool,
    reason: str | None = None,
) -> MarketSale:
    """Include or exclude a sale by hand. The user's decision outranks the system's."""
    sale.is_excluded = excluded
    sale.excluded_by = "user"
    if excluded:
        sale.exclusion_reason = reason or SaleExclusionReason.USER_EXCLUDED.value
    else:
        sale.exclusion_reason = None
        sale.is_outlier = False
    db.flush()
    return sale


def migrate_card_key(db: Session, card_id: str, old_key: str, new_key: str) -> int:
    """Follow a card's own sales when its identity changes.

    Editing a card's language, variant or number changes its ``catalog_key``,
    and without this the sales entered against it stay behind on the old key and
    appear to have vanished.

    Only rows carrying this ``card_id`` move — sales imported against the
    identity generally stay where they are, because they describe that identity
    rather than this copy. Every moved row is re-judged by ``reclassify_key``
    immediately afterwards, which is what stops a correction from smuggling
    English comparables into a Japanese card's median.
    """
    if old_key == new_key:
        return 0
    rows = db.scalars(
        select(MarketSale).where(
            MarketSale.card_id == card_id, MarketSale.catalog_key == old_key
        )
    ).all()
    for row in rows:
        row.catalog_key = new_key
    for table in (MarketListing, PriceSnapshot):
        for row in db.scalars(
            select(table).where(table.card_id == card_id, table.catalog_key == old_key)
        ):
            row.catalog_key = new_key
    db.flush()
    return len(rows)


def reclassify_key(
    db: Session,
    *,
    context: SaleContext,
) -> dict[str, int]:
    """Re-run the heuristics over every system-classified sale for one identity.

    Editing a card's language or variant changes what counts as a mismatch, and
    the rules themselves change between versions. User decisions are untouched.
    """
    counts: dict[str, int] = {"kept": 0, "excluded": 0, "unchanged": 0, "skipped_user": 0}
    sales = db.scalars(
        select(MarketSale).where(MarketSale.catalog_key == context.catalog_key)
    ).all()
    for sale in sales:
        if sale.excluded_by == "user":
            counts["skipped_user"] += 1
            continue
        was_excluded = sale.is_excluded
        verdict = classify(
            title=sale.listing_title,
            context=context,
            lot_size=sale.lot_size,
            grade_label=sale.grade_label,
        )
        if verdict is None:
            # Outlier status is owned by ``mark_outliers``, not by the title rules.
            if sale.exclusion_reason == SaleExclusionReason.PRICE_OUTLIER.value:
                counts["unchanged"] += 1
                continue
            sale.is_excluded = False
            sale.exclusion_reason = None
            sale.excluded_by = None
        else:
            sale.is_excluded = True
            sale.exclusion_reason = verdict[0]
            sale.excluded_by = "system"
        if sale.is_excluded == was_excluded:
            counts["unchanged"] += 1
        elif sale.is_excluded:
            counts["excluded"] += 1
        else:
            counts["kept"] += 1
    db.flush()
    return counts


def mark_outliers(
    db: Session,
    catalog_key: str,
    *,
    params: market_service.MarketParameters,
) -> dict[str, int]:
    """Fence off absurd prices per grade, using the IQR of that grade's sales.

    Run per ``grade_label``, because a PSA 10 selling for ten times a raw copy
    is the normal state of affairs, not an outlier. Below
    ``min_sales_for_outliers`` the fence is not drawn at all.
    """
    counts = {"flagged": 0, "cleared": 0, "considered": 0}
    labels = market_service.grade_labels_for(db, catalog_key)
    for label in labels:
        sales = list(
            db.scalars(
                select(MarketSale).where(
                    MarketSale.catalog_key == catalog_key,
                    MarketSale.grade_label == label,
                )
            )
        )
        # Only title-clean sales define the fence; a job lot must not widen it.
        candidates = [
            sale
            for sale in sales
            if not sale.is_excluded
            or sale.exclusion_reason == SaleExclusionReason.PRICE_OUTLIER.value
        ]
        if len(candidates) < params.min_sales_for_outliers:
            for sale in candidates:
                if sale.exclusion_reason == SaleExclusionReason.PRICE_OUTLIER.value:
                    sale.is_excluded = False
                    sale.exclusion_reason = None
                    sale.excluded_by = None
                    sale.is_outlier = False
                    counts["cleared"] += 1
            continue

        counts["considered"] += len(candidates)
        bounds = market_service.iqr_bounds(
            [sale.sale_price_minor for sale in candidates], params.outlier_iqr_multiplier
        )
        if bounds is None:  # pragma: no cover - guarded by the length check
            continue
        low, high = bounds
        for sale in candidates:
            outside = sale.sale_price_minor < low or sale.sale_price_minor > high
            if sale.excluded_by == "user":
                continue
            if outside and not sale.is_outlier:
                sale.is_outlier = True
                sale.is_excluded = True
                sale.exclusion_reason = SaleExclusionReason.PRICE_OUTLIER.value
                sale.excluded_by = "system"
                counts["flagged"] += 1
            elif not outside and sale.is_outlier:
                sale.is_outlier = False
                sale.is_excluded = False
                sale.exclusion_reason = None
                sale.excluded_by = None
                counts["cleared"] += 1
    db.flush()
    return counts
