"""add completed quote status

Revision ID: f67f76157b1f
Revises: b9725939d1bc
Create Date: 2026-11-20 16:50:02.583294

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f67f76157b1f'
down_revision: str | Sequence[str] | None = 'b9725939d1bc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Written by hand: the two databases store enums differently.
    if op.get_bind().dialect.name == "postgresql":
        # PostgreSQL has a real enum type: add the new value to it.
        op.execute("ALTER TYPE quotestatus ADD VALUE IF NOT EXISTS 'completed'")
    else:
        # SQLite stores the enum as text sized to the longest value. "completed"
        # (9 letters) is longer than "accepted"/"declined" (8), so widen the column.
        with op.batch_alter_table("quote_requests") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.VARCHAR(length=8),
                type_=sa.Enum(
                    "new", "quoted", "accepted", "declined", "completed", name="quotestatus"
                ),
                existing_nullable=False,
            )


def downgrade() -> None:
    """Downgrade schema."""
    # PostgreSQL cannot remove a value from an enum type, so there is nothing to undo
    # there; on SQLite the wider column is harmless.
