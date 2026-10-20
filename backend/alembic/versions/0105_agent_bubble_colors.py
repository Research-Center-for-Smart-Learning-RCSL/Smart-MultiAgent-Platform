"""Optional per-room participant bubble colours ([R13.78])."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0105_agent_bubble_colors"
down_revision: str = "0104_room_wakeup_layers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLOR = pg.ENUM(
    "cyan",
    "teal",
    "green",
    "lime",
    "amber",
    "orange",
    "rose",
    "violet",
    name="chatroom_agent_bubble_color",
    create_type=False,
)
_CHECK = "ck_chatroom_agents_normal_bubble_color"


def upgrade() -> None:
    _COLOR.create(op.get_bind(), checkfirst=True)
    op.add_column("chatroom_agents", sa.Column("bubble_color", _COLOR, nullable=True))
    op.create_check_constraint(_CHECK, "chatroom_agents", "bubble_color IS NULL OR role = 'normal'")


def downgrade() -> None:
    op.drop_constraint(_CHECK, "chatroom_agents", type_="check")
    op.drop_column("chatroom_agents", "bubble_color")
    _COLOR.drop(op.get_bind(), checkfirst=True)
