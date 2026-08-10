"""Application settings and metadata."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import DbSession
from app.api.errors import ApiError
from app.enums import CORNER_FIELDS, DEFECT_FIELDS, ENUM_REGISTRY
from app.schemas.common import ApiModel, EnumsResponse
from app.services import settings_service
from app.services.settings_service import UnknownSettingError

router = APIRouter(tags=["settings"])


class SettingDefinitionOut(ApiModel):
    key: str
    label: str
    type: str
    default: Any
    category: str
    description: str
    minimum: float | None
    maximum: float | None
    options: list[str]
    advanced: bool


class SettingsResponse(ApiModel):
    values: dict[str, Any]
    definitions: list[SettingDefinitionOut]


class SettingsUpdate(ApiModel):
    values: dict[str, Any]


@router.get("/settings", response_model=SettingsResponse, summary="Get settings and their definitions")
def get_settings(db: DbSession) -> SettingsResponse:
    return SettingsResponse(
        values=settings_service.get_all(db),
        definitions=[
            SettingDefinitionOut(
                key=d.key,
                label=d.label,
                type=d.type,
                default=d.default,
                category=d.category,
                description=d.description,
                minimum=d.minimum,
                maximum=d.maximum,
                options=list(d.options),
                advanced=d.advanced,
            )
            for d in settings_service.SETTING_DEFINITIONS
        ],
    )


@router.patch("/settings", response_model=SettingsResponse, summary="Update settings")
def update_settings(db: DbSession, payload: SettingsUpdate) -> SettingsResponse:
    try:
        settings_service.set_many(db, payload.values)
    except UnknownSettingError as exc:
        raise ApiError("unknown_setting", f"'{exc.args[0]}' is not a known setting.") from exc
    except ValueError as exc:
        raise ApiError("invalid_setting", str(exc)) from exc
    return get_settings(db)


@router.post("/settings/{key}/reset", response_model=SettingsResponse, summary="Reset one setting")
def reset_setting(db: DbSession, key: str) -> SettingsResponse:
    try:
        settings_service.reset(db, key)
    except UnknownSettingError as exc:
        raise ApiError("unknown_setting", f"'{key}' is not a known setting.") from exc
    return get_settings(db)


@router.get(
    "/meta/enums",
    response_model=EnumsResponse,
    summary="Every controlled vocabulary",
    description="The UI builds its dropdowns from this so no vocabulary is duplicated client-side.",
)
def get_enums() -> EnumsResponse:
    return EnumsResponse(
        enums={name: enum.values() for name, enum in ENUM_REGISTRY.items()},
        defect_fields=list(DEFECT_FIELDS),
        corner_fields=list(CORNER_FIELDS),
    )
