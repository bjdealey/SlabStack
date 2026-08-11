"""Controlled vocabularies.

These are Python enums used for validation and for the ``/api/meta/enums``
endpoint that the UI reads to build its dropdowns. They are stored in SQLite as
``TEXT`` with ``CHECK`` constraints rather than native enums so that adding a
value is a migration, not a table rebuild.

Deliberately *not* enums: grading company codes, tier names, selling platforms
and market-data sources. Those are rows in configuration tables because grading
prices and fee structures change several times a year (spec sections 10, 14, 22).
"""

from __future__ import annotations

import enum


class StrEnum(enum.StrEnum):
    """``enum.StrEnum`` plus ``values()``, which every route and check constraint uses."""

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]


class Language(StrEnum):
    ENGLISH = "English"
    JAPANESE = "Japanese"
    GERMAN = "German"
    FRENCH = "French"
    ITALIAN = "Italian"
    SPANISH = "Spanish"
    PORTUGUESE = "Portuguese"
    KOREAN = "Korean"
    CHINESE = "Chinese"
    DUTCH = "Dutch"
    POLISH = "Polish"
    RUSSIAN = "Russian"
    THAI = "Thai"
    INDONESIAN = "Indonesian"
    OTHER = "Other"


class Printing(StrEnum):
    UNLIMITED = "Unlimited"
    FIRST_EDITION = "1st Edition"
    SHADOWLESS = "Shadowless"
    UNNUMBERED = "Unnumbered Promo"
    STAFF = "Staff"
    PRERELEASE = "Prerelease"
    OTHER = "Other"


class RawCondition(StrEnum):
    """Coarse raw grade. Kept only as a quick label — the real signal lives in
    ``condition_assessments`` (spec section 6 explicitly rejects NM/LP/MP as the
    condition model)."""

    GEM_MINT = "Gem Mint"
    MINT = "Mint"
    NEAR_MINT = "Near Mint"
    LIGHTLY_PLAYED = "Lightly Played"
    MODERATELY_PLAYED = "Moderately Played"
    HEAVILY_PLAYED = "Heavily Played"
    DAMAGED = "Damaged"
    UNKNOWN = "Unknown"


class CardStatus(StrEnum):
    IN_COLLECTION = "in_collection"
    SUBMITTED_FOR_GRADING = "submitted_for_grading"
    GRADED = "graded"
    LISTED_FOR_SALE = "listed_for_sale"
    SOLD = "sold"
    ARCHIVED = "archived"


class ImageSide(StrEnum):
    FRONT = "front"
    BACK = "back"
    DETAIL = "detail"
    SLAB = "slab"


class Severity(StrEnum):
    """Defect severity (spec section 6)."""

    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    UNKNOWN = "unknown"


class Assessor(StrEnum):
    USER = "user"
    IMAGE_MODEL = "image_model"
    IMPORTED = "imported"


class PredictionKind(StrEnum):
    """Spec section 8 requires these to stay distinguishable."""

    PHYSICAL = "physical"  # "based on the observed defects"
    MARKET = "market"  # "condition + known grading behaviour"


class PredictionSource(StrEnum):
    RULES_ENGINE = "rules_engine"
    USER_OVERRIDE = "user_override"
    CALIBRATED = "calibrated"  # rules engine adjusted by the user's own history
    IMAGE_MODEL = "image_model"


class Confidence(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Decision(StrEnum):
    """The recommendation vocabulary (spec sections 14, 25, 31)."""

    GRADE = "grade"
    GRADE_IF_BATCH_FILLED = "grade_if_batch_filled"
    SELL_RAW = "sell_raw"
    KEEP_RAW = "keep_raw"
    HOLD = "hold"
    DO_NOT_GRADE = "do_not_grade"
    INSUFFICIENT_DATA = "insufficient_data"


class RiskTolerance(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class TrendDirection(StrEnum):
    STRONG_UP = "strong_up"
    UP = "up"
    STABLE = "stable"
    DOWN = "down"
    STRONG_DOWN = "strong_down"
    INSUFFICIENT_DATA = "insufficient_data"


class LiquidityBand(StrEnum):
    VERY_LIQUID = "very_liquid"
    LIQUID = "liquid"
    MODERATE = "moderate"
    ILLIQUID = "illiquid"
    VERY_ILLIQUID = "very_illiquid"
    UNKNOWN = "unknown"


class CostAllocationMethod(StrEnum):
    EQUAL = "equal"
    VALUE_WEIGHTED = "value_weighted"
    CUSTOM = "custom"


class DeclaredValueSource(StrEnum):
    SYSTEM = "system"
    USER = "user"


class SubmissionStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    SHIPPED = "shipped"
    RECEIVED = "received"
    GRADING = "grading"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class SubmissionCardStatus(StrEnum):
    PLANNED = "planned"
    SUBMITTED = "submitted"
    GRADED = "graded"
    RETURNED = "returned"
    REJECTED = "rejected"
    REMOVED = "removed"


class GroupKind(StrEnum):
    FOLDER = "folder"
    WATCHLIST = "watchlist"
    SMART = "smart"


class DataSourceKind(StrEnum):
    MARKET_DATA = "market_data"
    CARD_CATALOG = "card_catalog"
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"


class SaleExclusionReason(StrEnum):
    """Why a comparable sale was removed from the calculation (spec section 15)."""

    LOT_OR_BUNDLE = "lot_or_bundle"
    DAMAGED = "damaged"
    WRONG_CARD = "wrong_card"
    WRONG_LANGUAGE = "wrong_language"
    WRONG_VARIANT = "wrong_variant"
    WRONG_GRADE = "wrong_grade"
    PRICE_OUTLIER = "price_outlier"
    SUSPECTED_FAKE = "suspected_fake"
    BEST_OFFER_UNKNOWN = "best_offer_unknown"
    USER_EXCLUDED = "user_excluded"


class BlockStatus(StrEnum):
    """Status of one block inside an ``evaluate_card`` response."""

    OK = "ok"
    PARTIAL = "partial"
    NOT_ASSESSED = "not_assessed"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_IMPLEMENTED = "not_implemented"


# The set of defect fields assessed on each face of the card (spec section 6).
DEFECT_FIELDS: tuple[str, ...] = (
    "corner_tl",
    "corner_tr",
    "corner_bl",
    "corner_br",
    "edge_condition",
    "surface_condition",
    "holo_condition",
    "scratches",
    "print_lines",
    "silvering",
    "whitening",
    "dents",
    "dimpling",
    "creases",
    "staining",
    "misc_defects",
)

CORNER_FIELDS: tuple[str, ...] = ("corner_tl", "corner_tr", "corner_bl", "corner_br")

ENUM_REGISTRY: dict[str, type[StrEnum]] = {
    "language": Language,
    "printing": Printing,
    "raw_condition": RawCondition,
    "card_status": CardStatus,
    "image_side": ImageSide,
    "severity": Severity,
    "assessor": Assessor,
    "prediction_kind": PredictionKind,
    "prediction_source": PredictionSource,
    "confidence": Confidence,
    "decision": Decision,
    "risk_tolerance": RiskTolerance,
    "trend_direction": TrendDirection,
    "liquidity_band": LiquidityBand,
    "cost_allocation_method": CostAllocationMethod,
    "declared_value_source": DeclaredValueSource,
    "submission_status": SubmissionStatus,
    "submission_card_status": SubmissionCardStatus,
    "group_kind": GroupKind,
    "data_source_kind": DataSourceKind,
    "sale_exclusion_reason": SaleExclusionReason,
    "block_status": BlockStatus,
}
