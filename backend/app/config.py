"""Application configuration.

SlabStack is local-first: every path below resolves inside the user's own
machine and no setting here points at a third-party service. Market-data
provider credentials live in the ``data_sources`` table (Phase 3), not here,
so that the application keeps working with no network at all.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


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
