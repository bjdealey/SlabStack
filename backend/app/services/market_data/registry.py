"""Provider registry.

Adapters are resolved from the ``data_sources`` table by dotted path, so adding
a provider in Phase 3 means writing one class and enabling one row — no changes
to the engine, the API or the UI.
"""

from __future__ import annotations

import importlib
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSource
from app.services.market_data.base import MarketDataProvider


class ProviderUnavailableError(RuntimeError):
    pass


def load_provider(source: DataSource) -> MarketDataProvider:
    if not source.enabled:
        raise ProviderUnavailableError(f"Data source '{source.code}' is disabled.")
    if not source.provider_class:
        raise ProviderUnavailableError(
            f"Data source '{source.code}' has no adapter yet (arrives in Phase 3)."
        )

    module_path, _, class_name = source.provider_class.rpartition(".")
    try:
        module = importlib.import_module(module_path)
        provider_class = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ProviderUnavailableError(
            f"Adapter '{source.provider_class}' for '{source.code}' could not be loaded."
        ) from exc

    api_key = os.environ.get(source.api_key_env_var) if source.api_key_env_var else None
    if source.api_key_env_var and not api_key:
        raise ProviderUnavailableError(
            f"Data source '{source.code}' needs {source.api_key_env_var} in the environment."
        )
    return provider_class(config=source.config or {}, api_key=api_key)


def enabled_sources(db: Session) -> list[DataSource]:
    return list(
        db.scalars(
            select(DataSource).where(DataSource.enabled.is_(True)).order_by(DataSource.priority)
        )
    )
