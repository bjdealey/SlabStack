"""add a user declared value to cards

What to tell the grader a card is worth, kept apart from the raw value because
they answer different questions: one is what you would sell it for today, the
other is what you insure the slab for. The engine's own suggestion is never
written here — it is computed per evaluation, so this column holds only a
figure the user typed.

Nullable, so it needs no server default and applies cleanly to a populated
table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-11 12:54:54.732415+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_declared_value_minor", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.drop_column("user_declared_value_minor")
