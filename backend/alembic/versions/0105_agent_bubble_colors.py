"""Optional per-room participant bubble colours ([R13.78]).

The CHECK keeps "an observer binding has no colour" true at rest. Pre-0105 code
demotes a binding without touching `bubble_color`, so on its own the CHECK would
turn that UPDATE into an IntegrityError after a code rollback or on a replica
not yet upgraded. The BEFORE trigger clears the colour on any change of role to
observer first, which keeps old code working on this schema while a direct write
of a colour onto an observer still violates the CHECK.
"""

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
_FUNCTION = "smap_chatroom_agents_clear_observer_color"
_TRIGGER = "trg_chatroom_agents_clear_observer_color"


def upgrade() -> None:
    _COLOR.create(op.get_bind(), checkfirst=True)
    op.add_column("chatroom_agents", sa.Column("bubble_color", _COLOR, nullable=True))
    op.create_check_constraint(_CHECK, "chatroom_agents", "bubble_color IS NULL OR role = 'normal'")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          NEW.bubble_color := NULL;
          RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        f"CREATE TRIGGER {_TRIGGER} "
        "BEFORE UPDATE OF role ON chatroom_agents "
        "FOR EACH ROW WHEN (NEW.role = 'observer' AND OLD.role IS DISTINCT FROM NEW.role) "
        f"EXECUTE FUNCTION {_FUNCTION}();"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON chatroom_agents")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
    op.drop_constraint(_CHECK, "chatroom_agents", type_="check")
    op.drop_column("chatroom_agents", "bubble_color")
    _COLOR.drop(op.get_bind(), checkfirst=True)
