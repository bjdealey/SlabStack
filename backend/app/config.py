"""Application configuration.

SlabStack is local-first: every path below resolves inside the user's own
machine and no setting here points at a third-party service. Market-data
provider credentials live in the ``data_sources`` table (Phase 3), not here,
so that the application keeps working with no network at all.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

#: Files that may hold local configuration, nearest last so it wins.
ENV_FILES = (REPO_ROOT / ".env", BACKEND_ROOT / ".env")


def load_env_files() -> list[str]:
    """Put ``.env`` into the real environment, not only into ``Settings``.

    Pydantic reads ``.env`` into *this model* and ignores anything that is not a
    declared field. Provider credentials are not declared fields and cannot be:
    which variable a source reads is a row in the ``data_sources`` table, chosen
    at runtime, so this class has no way to know the names at import time. They
    are looked up with ``os.environ.get`` when a request is about to be made.

    Without this, a key written into ``.env`` was silently dropped for every
    provider, and the UI reported it as "not set" while the user was looking
    straight at the line they had just added. Docker Compose does its own
    ``.env`` substitution and so was never affected — which made the whole thing
    worse, because it meant the same file worked or didn't depending on how you
    started the application, with nothing to suggest why.

    An already-exported variable wins. The shell is the more deliberate
    statement of the two, and a stale ``.env`` should never quietly override the
    key someone just set on the command line.
    """
    loaded: list[str] = []
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for key, value in dotenv_values(path).items():
            if value is None or key in os.environ:
                continue
            os.environ[key] = value
            loaded.append(key)
    return loaded


def env_file_report() -> list[dict]:
    """What each ``.env`` file contributes, for ``make doctor``.

    Separate from loading, because by the time anything asks, loading has
    already happened and a variable from a file is indistinguishable from one
    you exported. The failure this exists to name is the nasty one: a file that
    is present, looks right, and yields nothing.

    Two ways that happens, both invisible while you stare at the file:

    * every line is still commented out after copying ``.env.example``;
    * it starts with a UTF-8 byte-order mark, which some editors add silently
      and which fuses onto the first variable name so it matches nothing.

    Names only, never values — the same rule the rest of this build follows.
    """
    report: list[dict] = []
    for path in ENV_FILES:
        if not path.is_file():
            report.append({"path": str(path), "exists": False, "keys": [], "bom": False})
            continue
        report.append(
            {
                "path": str(path),
                "exists": True,
                "keys": sorted(key for key, value in dotenv_values(path).items() if value),
                "bom": path.read_bytes().startswith(b"\xef\xbb\xbf"),
            }
        )
    return report


#: Which variables came from a file rather than from the shell. Recorded at
#: import, because afterwards the two are indistinguishable.
LOADED_FROM_ENV_FILES: list[str] = load_env_files()


class Settings(BaseSettings):
    """Runtime configuration, overridable via ``SLABSTACK_*`` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="SLABSTACK_",
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SlabStack"
    environment: str = "local"
    debug: bool = True

    # --- Storage -----------------------------------------------------------
    data_dir: Path = Field(default=BACKEND_ROOT / "data")
    database_url: str | None = None

    # --- HTTP --------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )

    # --- Media -------------------------------------------------------------
    max_image_bytes: int = 25 * 1024 * 1024
    thumbnail_max_px: int = 480
    allowed_image_types: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )

    # --- Static UI ---------------------------------------------------------
    # When the built frontend is present, the API serves it too, so a packaged
    # install is one process on one port instead of two.
    static_dir: Path | None = None

    # --- Domain defaults ---------------------------------------------------
    default_currency: str = "GBP"

    @field_validator("data_dir", "static_dir", mode="after")
    @classmethod
    def _absolute(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value if value.is_absolute() else (BACKEND_ROOT / value).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "slabstack.db"

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or f"sqlite+pysqlite:///{self.db_path}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def resolved_static_dir(self) -> Path | None:
        """Where the built UI lives, if it has been built.

        Returns ``None`` in development, where Vite serves the UI and proxies
        ``/api`` here — so the same code runs both ways with no flag.
        """
        candidate = self.static_dir or (REPO_ROOT / "frontend" / "dist")
        return candidate if (candidate / "index.html").exists() else None

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


settings = get_settings()
