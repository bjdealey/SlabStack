"""Endpoints whose engines arrive in later phases.

They are registered now, returning 501 with the phase attached, for two reasons:
the API contract stays executable rather than aspirational, and the UI can show
"arrives in Phase 4" instead of a broken request. Removing a stub is a matter of
replacing its body — the path and response model do not change.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.errors import NotImplementedYetError

router = APIRouter(tags=["not yet implemented"])


def _stub(message: str, phase: int, planned_in: str) -> None:
    raise NotImplementedYetError(message, phase=phase, planned_in=planned_in)


@router.post("/cards/identify", summary="Identify a card from images (Phase 3)")
def identify_card() -> None:
    _stub(
        "Image-assisted identification is not built yet. Identify cards manually for now — "
        "and note that identification will always be a suggestion you confirm, never applied "
        "silently.",
        phase=3,
        planned_in="Phase 3 — CardIdentificationProvider abstraction.",
    )


@router.post("/market/refresh", summary="Fetch sales from a data provider (Phase 3)")
def refresh_market() -> None:
    _stub(
        "No network market-data provider is connected. Valuation, liquidity and trend are "
        "built and work today on sales you import: enter them by hand or import a CSV export "
        "from your marketplace. Provider adapters need an API key and each service's terms "
        "reviewed, and the local database is the source of truth either way — a provider only "
        "ever writes into it.",
        phase=3,
        planned_in="Phase 3 — MarketDataProvider adapters, once API credentials exist.",
    )
