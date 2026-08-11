"""freeze the whole predicted distribution on a submission line

``predicted_grade`` already records what the model called most likely when the
card joined the parcel. That is enough to say "predicted 9.5, got 10" and not
enough to score the prediction: a Brier score marks the whole distribution, and
being 55% on a 10 is a different claim from being 95% on a 10 even though both
have the same mode.

So the distribution is frozen alongside it, for the same reason the scalar is:
what gets marked has to be the belief actually held when the card was sent, not
one recomputed after the grade is known.

Nullable, because lines added before this migration have no distribution behind
them and inventing one would be the exact dishonesty this column exists to
avoid. Those lines stay unscoreable and say so.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-11 19:04:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("submission_cards", schema=None) as batch_op:
        batch_op.add_column(sa.Column("predicted_probabilities", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("submission_cards", schema=None) as batch_op:
        batch_op.drop_column("predicted_probabilities")
