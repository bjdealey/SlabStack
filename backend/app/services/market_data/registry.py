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

    config = source.config or {}
    api_key = os.environ.get(source.api_key_env_var) if source.api_key_env_var else None
    # Some providers take a key but do not require one — pokemontcg.io works
    # anonymously at a lower rate limit. Refusing to start without a key there
    # would block the one source a user can try with no signup at all.
    optional = bool(config.get("api_key_optional"))
    if source.api_key_env_var and not api_key and not optional:
        raise ProviderUnavailableError(
            f"Data source '{source.code}' needs {source.api_key_env_var} in the environment."
        )
    return provider_class(config=config, api_key=api_key)


def credential_names(source: DataSource) -> list[str]:
    """Every environment variable this source reads, in the order it needs them.

    Most sources want one key. eBay wants a client id *and* a client secret, and
    reporting only the first would put a green tick beside a source that cannot
    authenticate. Extra ones are declared in ``config`` under a ``*_env_var``
    key — the name, never the value, which is the rule that keeps the database
    safe to copy around.
    """
    names: list[str] = []
    if source.api_key_env_var:
        names.append(source.api_key_env_var)
    for key, value in (source.config or {}).items():
        if key.endswith("_env_var") and isinstance(value, str) and value not in names:
            names.append(value)
    return names


def credentials_present(source: DataSource) -> dict[str, bool]:
    """Which of them are actually set, for the UI and the doctor."""
    return {name: bool(os.environ.get(name)) for name in credential_names(source)}


def enabled_sources(db: Session) -> list[DataSource]:
    return list(
        db.scalars(
            select(DataSource).where(DataSource.enabled.is_(True)).order_by(DataSource.priority)
        )
    )
