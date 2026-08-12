"""record what a card actually sold for

Everything else in this application is a projection: what a card is worth, what
grading would cost, what a sale would net. Until now nothing recorded what
happened, so the app could learn whether its *grade* predictions were right —
``prediction_results`` has done that since Phase 8 — and never whether its
*profit* predictions were. That is a strange gap in a build whose first
principle is realisable profit rather than theoretical value.

A table rather than columns on ``cards``, for two reasons. A sale has its own
shape — gross, fees, postage, what grading cost to get there — and hanging nine
more money columns off ``cards`` would spread the sale across a row that is
mostly about identity. And ``catalog_key`` and ``card_name`` are denormalised
here on purpose, with ``card_id`` nullable: deleting a card should lose the
card, not the lesson.

``net_proceeds_minor`` is stored rather than derived at read time because it is
the one figure that is *not* a projection. It can be computed from the
components, but when a payout statement gives a single number that number is the
truth and the components are the estimate — so ``net_is_user_entered`` keeps the
two apart, the way every other override in this schema does.

``grading_cost_minor`` is nullable and null means *unrecorded*, never free. A
realised profit computed without it would flatter grading, which is exactly the
bias this application exists to correct.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-12 23:05:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "card_disposals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("card_id", sa.String(length=32), nullable=True),
        sa.Column("catalog_key", sa.String(length=200), nullable=True),
        sa.Column("card_name", sa.String(length=160), nullable=True),
        sa.Column("sold_on", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=48), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="GBP"),
        sa.Column("sold_graded", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("company_id", sa.String(length=32), nullable=True),
        sa.Column("grade", sa.Float(), nullable=True),
        sa.Column("grade_label", sa.String(length=24), nullable=False, server_default="raw"),
        sa.Column("gross_minor", sa.Integer(), nullable=False),
        sa.Column("shipping_income_minor", sa.Integer(), nullable=True),
        sa.Column("fees_minor", sa.Integer(), nullable=True),
        sa.Column("postage_cost_minor", sa.Integer(), nullable=True),
        sa.Column("packaging_cost_minor", sa.Integer(), nullable=True),
        sa.Column("net_proceeds_minor", sa.Integer(), nullable=False),
        sa.Column(
            "net_is_user_entered", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("grading_cost_minor", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["company_id"], ["grading_companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_card_disposals_card_id", "card_disposals", ["card_id"])
    op.create_index("ix_card_disposals_catalog_key", "card_disposals", ["catalog_key"])


def downgrade() -> None:
    op.drop_index("ix_card_disposals_catalog_key", table_name="card_disposals")
    op.drop_index("ix_card_disposals_card_id", table_name="card_disposals")
    op.drop_table("card_disposals")
