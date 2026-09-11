"""Full-text search index for canvas objects ([R13.62]).

Adds a `content_tsv` TSVECTOR column to `canvas_objects`, maintained by a
BEFORE trigger on INSERT/UPDATE OF content.  Only objects with non-null
`content` (note, text kinds) produce index entries; others stay NULL.

GIN index for the `@@` operator, plus a backfill of existing rows.

Reversible: drop trigger, function, index, column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0093_canvas_search"
down_revision: str = "0092_canvas_snapshot_label"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canvas_objects",
        sa.Column("content_tsv", pg.TSVECTOR(), nullable=True),
    )

    op.execute("CREATE INDEX ix_canvas_objects_content_tsv ON canvas_objects USING GIN (content_tsv)")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION smap_canvas_objects_tsv() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.content IS NOT NULL THEN
            NEW.content_tsv := to_tsvector('english', NEW.content);
          ELSE
            NEW.content_tsv := NULL;
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_canvas_objects_tsv "
        "BEFORE INSERT OR UPDATE OF content ON canvas_objects "
        "FOR EACH ROW EXECUTE FUNCTION smap_canvas_objects_tsv();"
    )

    op.execute(
        "UPDATE canvas_objects SET content_tsv = to_tsvector('english', content) WHERE content IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_canvas_objects_tsv ON canvas_objects")
    op.execute("DROP FUNCTION IF EXISTS smap_canvas_objects_tsv()")
    op.execute("DROP INDEX IF EXISTS ix_canvas_objects_content_tsv")
    op.drop_column("canvas_objects", "content_tsv")
