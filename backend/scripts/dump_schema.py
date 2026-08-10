"""Emit ``docs/schema.sql`` from the SQLAlchemy metadata.

The models are the source of truth; this produces the readable SQL that goes in
the docs so the schema can be reviewed without reading Python.

    python -m scripts.dump_schema
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base

HEADER = """-- SlabStack database schema (SQLite)
--
-- GENERATED FILE — do not edit.
-- Source of truth: backend/app/models/*.py
-- Regenerate with: cd backend && python -m scripts.dump_schema
--
-- Conventions:
--   *_minor      integer count of minor currency units (pence), never a float
--   catalog_key  normalised card identity shared by duplicate copies
--   is_current   marks the live row where history is kept (condition, predictions)
"""


def render() -> str:
    from sqlalchemy.dialects import sqlite

    dialect = sqlite.dialect()
    parts: list[str] = [HEADER]

    for table in Base.metadata.sorted_tables:
        parts.append(f"\n-- {'=' * 68}\n-- {table.name}\n-- {'=' * 68}")
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")

    return "\n".join(parts) + "\n"


def main() -> None:
    target = Path(__file__).resolve().parents[2] / "docs" / "schema.sql"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    print(f"Wrote {target} ({len(Base.metadata.tables)} tables)")


if __name__ == "__main__":
    main()
