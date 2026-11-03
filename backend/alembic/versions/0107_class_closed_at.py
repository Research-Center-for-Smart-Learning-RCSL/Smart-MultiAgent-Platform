"""Class close ([R15.30]).

One nullable column that nothing pre-0107 reads, so old code runs on this schema
unchanged. No index: every read is by primary key or rides the silence sweep's
existing join on chatrooms.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0107_class_closed_at"
down_revision: str = "0106_class_start_and_time_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chatrooms", sa.Column("class_closed_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("chatrooms", "class_closed_at")
