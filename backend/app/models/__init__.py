"""SQLAlchemy models — the single source of truth for the schema.

``docs/schema.sql`` is generated from this metadata (``make schema``) and the
Alembic migration in ``alembic/versions`` is generated from it too.
"""

from app.models.base import Base
from app.models.card import Card, CardImage, CollectionGroup, CollectionGroupCard
from app.models.catalog import CardSet, CardVariant
from app.models.condition import (
    ConditionAssessment,
    GradePrediction,
    GradeRule,
    PredictionResult,
)
from app.models.economics import AppSetting, CardDisposal, SellingCostProfile
from app.models.grading import (
    GradingCompany,
    GradingMembership,
    GradingSubmission,
    GradingTier,
    SubmissionCard,
)
from app.models.market import (
    DataSource,
    MarketListing,
    MarketPrice,
    MarketSale,
    PriceSnapshot,
)

__all__ = [
    "AppSetting",
    "Base",
    "Card",
    "CardDisposal",
    "CardImage",
    "CardSet",
    "CardVariant",
    "CollectionGroup",
    "CollectionGroupCard",
    "ConditionAssessment",
    "DataSource",
    "GradePrediction",
    "GradeRule",
    "GradingCompany",
    "GradingMembership",
    "GradingSubmission",
    "GradingTier",
    "MarketListing",
    "MarketPrice",
    "MarketSale",
    "PredictionResult",
    "PriceSnapshot",
    "SellingCostProfile",
    "SubmissionCard",
]
