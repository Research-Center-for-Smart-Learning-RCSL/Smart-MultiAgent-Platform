"""Add display_name to chatroom_guests.

Revision ID: 0031_guest_display_name
Revises: 0030_agent_model_id

Nullable TEXT column (max 100 chars) so guests can set a display name
when enrolling via a guest link.
"""

revision = "0031_guest_display_name"
down_revision = "0030_agent_model_id"

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("chatroom_guests", sa.Column("display_name", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("chatroom_guests", "display_name")
