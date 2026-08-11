"""User-configurable economics and risk settings (spec sections 41, 42, 27).

Defaults live here as declarations rather than in the database, so a new setting
appears with a sane value on upgrade without a data migration. Only keys the
user has actually changed are written to ``app_settings``.

Money-valued settings are stored in **major** units (e.g. ``25.0`` = £25). They
are user-facing thresholds, not accumulating sums, and are converted once with
``money.to_minor()`` at the point of use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import CostAllocationMethod, RiskTolerance
from app.models import AppSetting

SettingType = Literal["string", "number", "integer", "boolean", "money", "percent", "enum", "json"]

# Kept here rather than imported from the model to avoid a circular import: the
# prediction service reads its parameters from this module.
DEFAULT_GRADE_WEIGHTS: dict[str, float] = {
    "centering": 0.25,
    "corners": 0.25,
    "edges": 0.20,
    "surface": 0.30,
}


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    type: SettingType
    default: Any
    category: str
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    options: list[str] = field(default_factory=list)
    advanced: bool = False


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    # --- General -----------------------------------------------------------
    SettingDefinition(
        key="currency",
        label="Currency",
        type="enum",
        default="GBP",
        category="general",
        options=["GBP", "USD", "EUR", "CAD", "AUD"],
        description="Currency all values are reported in.",
    ),
    SettingDefinition(
        key="default_selling_profile_code",
        label="Default selling platform",
        type="string",
        default="ebay_uk",
        category="general",
        description="Selling cost profile used when a card has no platform of its own.",
    ),
    SettingDefinition(
        key="default_grading_company_codes",
        label="Grading companies to compare",
        type="json",
        default=["PSA", "CGC", "ACE"],
        category="general",
        description="Companies the decision engine evaluates for every card.",
    ),
    # --- Profit thresholds -------------------------------------------------
    SettingDefinition(
        key="minimum_roi_pct",
        label="Minimum ROI",
        type="percent",
        default=25.0,
        category="thresholds",
        minimum=0,
        maximum=1000,
        description="Grading must beat this return on the money put at risk.",
    ),
    SettingDefinition(
        key="desired_profit_margin_pct",
        label="Desired profit margin",
        type="percent",
        default=30.0,
        category="thresholds",
        minimum=0,
        maximum=100,
    ),
    SettingDefinition(
        key="minimum_absolute_profit",
        label="Minimum absolute profit",
        type="money",
        default=25.0,
        category="thresholds",
        minimum=0,
        description="A 300% ROI on a £3 card is still not worth the postage.",
    ),
    SettingDefinition(
        key="minimum_probability_of_profit",
        label="Minimum probability of profit",
        type="percent",
        default=60.0,
        category="thresholds",
        minimum=0,
        maximum=100,
        description="How often the grade has to land profitably before it is a Grade call.",
    ),
    SettingDefinition(
        key="grading_value_floor",
        label="Do not consider grading below",
        type="money",
        default=15.0,
        category="thresholds",
        minimum=0,
        description="Raw value under which grading is never evaluated.",
    ),
    SettingDefinition(
        key="quick_sale_discount_pct",
        label="Quick-sale discount",
        type="percent",
        default=10.0,
        category="thresholds",
        minimum=0,
        maximum=90,
        description="Haircut applied to the realistic price when selling fast.",
    ),
    # --- Risk --------------------------------------------------------------
    SettingDefinition(
        key="risk_tolerance",
        label="Risk tolerance",
        type="enum",
        default=RiskTolerance.BALANCED.value,
        category="risk",
        options=RiskTolerance.values(),
        description="Shifts how much liquidity, confidence and variance are penalised.",
    ),
    SettingDefinition(
        key="minimum_liquidity_score",
        label="Minimum liquidity to recommend grading",
        type="number",
        default=3.0,
        category="risk",
        minimum=0,
        maximum=10,
    ),
    SettingDefinition(
        key="decision_score_weights",
        label="Decision score weights",
        type="json",
        default={
            "profitability": 35,
            "grade_probability": 25,
            "liquidity": 20,
            "trend": 10,
            "risk": 10,
        },
        category="risk",
        description="Weights of the composite Grading Opportunity Score. Must total 100.",
    ),
    SettingDefinition(
        key="hold_recheck_days",
        label="Hold recheck period",
        type="integer",
        default=30,
        category="risk",
        minimum=1,
        maximum=365,
    ),
    # --- Submission --------------------------------------------------------
    SettingDefinition(
        key="cost_allocation_method",
        label="Shared cost allocation",
        type="enum",
        default=CostAllocationMethod.EQUAL.value,
        category="submission",
        options=CostAllocationMethod.values(),
        description="How submission shipping and insurance are spread across cards.",
    ),
    SettingDefinition(
        key="default_submission_shipping_out",
        label="Default outbound shipping",
        type="money",
        default=20.0,
        category="submission",
        minimum=0,
    ),
    SettingDefinition(
        key="default_submission_shipping_return",
        label="Default return shipping",
        type="money",
        default=15.0,
        category="submission",
        minimum=0,
    ),
    SettingDefinition(
        key="default_submission_insurance_pct",
        label="Submission insurance",
        type="percent",
        default=1.0,
        category="submission",
        minimum=0,
        maximum=20,
        description="Percentage of total declared value charged to insure the parcel.",
    ),
    # --- Grade model (Phase 2) ---------------------------------------------
    # Our estimates, not any grader's published standard. Exposed so a user who
    # disagrees can say so rather than working around the model.
    SettingDefinition(
        key="grade_model_weights",
        label="Condition weighting",
        type="json",
        default=dict(DEFAULT_GRADE_WEIGHTS),
        category="grade_model",
        description="How much each sub-score counts toward the estimated grade.",
    ),
    SettingDefinition(
        key="grade_model_worst_weight",
        label="Weight on the worst attribute",
        type="number",
        default=0.45,
        category="grade_model",
        minimum=0,
        maximum=1,
        description=(
            "How much the weakest sub-score pulls the estimate down. At 0 the grade is a "
            "plain average, which lets three perfect attributes hide one bad one."
        ),
    ),
    SettingDefinition(
        key="grade_model_base_sigma",
        label="Grader inconsistency",
        type="number",
        default=0.45,
        category="grade_model",
        minimum=0.05,
        maximum=3,
        description=(
            "Spread that remains even on a fully assessed card, because the same card "
            "submitted twice does not always come back the same grade."
        ),
    ),
    SettingDefinition(
        key="grade_model_unknown_sigma",
        label="Spread added by an unfinished assessment",
        type="number",
        default=1.6,
        category="grade_model",
        minimum=0,
        maximum=5,
        description="Applied in proportion to how much of the assessment is unanswered.",
    ),
    SettingDefinition(
        key="grade_model_disagreement_factor",
        label="Spread added when sub-scores disagree",
        type="number",
        default=0.25,
        category="grade_model",
        minimum=0,
        maximum=2,
        advanced=True,
        description="A 10/10/10/6 card is less predictable than a 9/9/9/9 card.",
    ),
    SettingDefinition(
        key="grade_model_max_sigma",
        label="Maximum spread",
        type="number",
        default=3.0,
        category="grade_model",
        minimum=0.5,
        maximum=6,
        advanced=True,
    ),
    SettingDefinition(
        key="grade_model_min_probability",
        label="Drop grades below this probability",
        type="number",
        default=0.005,
        category="grade_model",
        minimum=0,
        maximum=0.2,
        advanced=True,
        description="Keeps the distribution readable by trimming negligible tails.",
    ),

    # --- Market analysis (used from Phase 3) -------------------------------
    SettingDefinition(
        key="market_window_days",
        label="Valuation window",
        type="integer",
        default=90,
        category="market",
        minimum=7,
        maximum=730,
        advanced=True,
    ),
    SettingDefinition(
        key="recency_half_life_days",
        label="Recency half-life",
        type="integer",
        default=45,
        category="market",
        minimum=1,
        maximum=365,
        advanced=True,
        description="Age at which a sale carries half the weight of a sale today.",
    ),
    SettingDefinition(
        key="outlier_iqr_multiplier",
        label="Outlier sensitivity (IQR multiplier)",
        type="number",
        default=1.5,
        category="market",
        minimum=0.5,
        maximum=5,
        advanced=True,
    ),
    SettingDefinition(
        key="min_sales_high_confidence",
        label="Sales needed for high confidence",
        type="integer",
        default=20,
        category="market",
        minimum=1,
        advanced=True,
    ),
    SettingDefinition(
        key="min_sales_medium_confidence",
        label="Sales needed for medium confidence",
        type="integer",
        default=8,
        category="market",
        minimum=1,
        advanced=True,
    ),
)

DEFINITIONS_BY_KEY: dict[str, SettingDefinition] = {d.key: d for d in SETTING_DEFINITIONS}


class UnknownSettingError(KeyError):
    pass


def get_all(db: Session) -> dict[str, Any]:
    """Every setting, defaults merged with the user's overrides."""
    values = {d.key: d.default for d in SETTING_DEFINITIONS}
    for row in db.scalars(select(AppSetting)):
        if row.key in values:
            values[row.key] = row.value
    return values


def get(db: Session, key: str) -> Any:
    if key not in DEFINITIONS_BY_KEY:
        raise UnknownSettingError(key)
    row = db.get(AppSetting, key)
    return row.value if row is not None else DEFINITIONS_BY_KEY[key].default


def set_many(db: Session, updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        definition = DEFINITIONS_BY_KEY.get(key)
        if definition is None:
            raise UnknownSettingError(key)
        _validate(definition, value)
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.flush()
    return get_all(db)


def reset(db: Session, key: str) -> Any:
    if key not in DEFINITIONS_BY_KEY:
        raise UnknownSettingError(key)
    row = db.get(AppSetting, key)
    if row is not None:
        db.delete(row)
        db.flush()
    return DEFINITIONS_BY_KEY[key].default


def _validate(definition: SettingDefinition, value: Any) -> None:
    if definition.type in {"number", "percent", "money"}:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{definition.key} must be a number")
    elif definition.type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{definition.key} must be an integer")
    elif definition.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{definition.key} must be a boolean")
    elif definition.type == "enum":
        if value not in definition.options:
            raise ValueError(f"{definition.key} must be one of {definition.options}")
    elif definition.type == "string" and not isinstance(value, str):
        raise ValueError(f"{definition.key} must be a string")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if definition.minimum is not None and value < definition.minimum:
            raise ValueError(f"{definition.key} must be >= {definition.minimum}")
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(f"{definition.key} must be <= {definition.maximum}")

    if definition.key == "grade_model_weights":
        if not isinstance(value, dict):
            raise ValueError("grade_model_weights must be an object")
        missing = set(DEFAULT_GRADE_WEIGHTS) - set(value)
        if missing:
            raise ValueError(f"grade_model_weights is missing {sorted(missing)}")
        weights = [float(value[key]) for key in DEFAULT_GRADE_WEIGHTS]
        if any(weight < 0 for weight in weights):
            raise ValueError("grade_model_weights cannot be negative")
        if sum(weights) <= 0:
            raise ValueError("grade_model_weights must not all be zero")

    if definition.key == "decision_score_weights":
        if not isinstance(value, dict):
            raise ValueError("decision_score_weights must be an object")
        missing = set(definition.default) - set(value)
        if missing:
            raise ValueError(f"decision_score_weights is missing {sorted(missing)}")
        total = sum(float(v) for v in value.values())
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"decision_score_weights must total 100 (got {total:g})")
