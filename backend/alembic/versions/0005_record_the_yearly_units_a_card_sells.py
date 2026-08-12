"""record the yearly units a source says a card sells

Liquidity has read ``unknown`` on every card since Phase 3, from every source,
because it is measured from individual sales and no obtainable source supplies
them. It is one of the five components of the Grading Opportunity Score, so the
score has been running on four.

PriceCharting's ``sales-volume`` is the first number any source has offered that
measures the thing directly: yearly units sold. Not sales records — a count —
which is why it needs a column of its own rather than being turned into
fabricated rows in ``market_sales``. A derived sale with an invented date would
corrupt trend, valuation and the outlier fence all at once, to answer a question
none of them asked.

It describes the *product*, pooled across grades, which is exactly the shape
liquidity is already measured at: ``summarise`` pools every grade for one
``catalog_key``. So it needs no attribution across grades and gets none.

Nullable, because almost nothing has it: only sources that report volume, only
for cards linked to them. A card without it keeps saying ``unknown``, which is
the truth.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-12 17:05:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("market_prices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("annual_volume", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("market_prices", schema=None) as batch_op:
        batch_op.drop_column("annual_volume")
