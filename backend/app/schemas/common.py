"""Shared response shapes.

Two envelopes, used everywhere:

* ``Page[T]`` for lists — never a bare array, so pagination can be added to any
  endpoint without breaking clients.
* ``ErrorEnvelope`` for failures — a stable ``code`` the UI can branch on, a
  ``message`` it can show, and ``details`` for field errors.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(items=items, total=total, page=page, page_size=page_size, total_pages=pages)


class ErrorDetail(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(ApiModel):
    error: ErrorDetail


class Acknowledgement(ApiModel):
    ok: bool = True
    message: str | None = None


class CountsResponse(ApiModel):
    counts: dict[str, int]


class ValueLabel(ApiModel):
    value: str
    label: str


class EnumsResponse(ApiModel):
    """Every controlled vocabulary, so the UI never hard-codes a dropdown."""

    enums: dict[str, list[str]]
    defect_fields: list[str]
    corner_fields: list[str]


class NotImplementedDetail(ApiModel):
    """Returned by endpoints whose phase has not been built yet."""

    code: str = "not_implemented"
    message: str
    phase: int
    planned_in: str = Field(description="Short description of what will implement this.")
