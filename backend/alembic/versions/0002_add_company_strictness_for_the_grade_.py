"""add company strictness for the grade model

Grade points a company is assumed to award above (+) or below (-) the model's
baseline. Ships at 0.0 for every company: SlabStack makes no claim about who
grades harder. The user tunes it from their own returned submissions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-11 09:57:57.509145+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default is required, not cosmetic: without it this NOT NULL column
    # cannot be added to a table that already has grading companies in it, which
    # every existing install does.
    with op.batch_alter_table("grading_companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("strictness", sa.Float(), nullable=False, server_default="0.0")
        )

    # The application supplies the default on insert from here on, so drop the
    # database-level one and keep the schema matching the models.
    with op.batch_alter_table("grading_companies", schema=None) as batch_op:
        batch_op.alter_column("strictness", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("grading_companies", schema=None) as batch_op:
        batch_op.drop_column("strictness")
