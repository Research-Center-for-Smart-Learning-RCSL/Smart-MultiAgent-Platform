"""Add canvas_templates table and seed platform templates.

Revision ID: 0094_canvas_templates
Revises: 0093_canvas_search
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0094_canvas_templates"
down_revision: str = "0093_canvas_search"


def _brainstorming_template() -> dict[str, Any]:
    """Central topic + 4 surrounding sticky notes + connectors."""
    center_id = str(uuid.uuid4())
    notes = []
    connectors = []
    positions = [
        (-250, -200, "Idea 1"),
        (250, -200, "Idea 2"),
        (-250, 200, "Idea 3"),
        (250, 200, "Idea 4"),
    ]
    for dx, dy, label in positions:
        nid = str(uuid.uuid4())
        notes.append({
            "id": nid,
            "kind": "note",
            "content": label,
            "position_x": 400 + dx,
            "position_y": 300 + dy,
            "width": 160,
            "height": 100,
            "z_index": 1,
            "style": {"backgroundColor": "#fef3c7"},
        })
        connectors.append({
            "id": str(uuid.uuid4()),
            "kind": "connector",
            "content": None,
            "position_x": 400,
            "position_y": 300,
            "width": abs(dx),
            "height": abs(dy),
            "z_index": 0,
            "style": {},
        })
    center = {
        "id": center_id,
        "kind": "note",
        "content": "Central Topic",
        "position_x": 400,
        "position_y": 300,
        "width": 180,
        "height": 100,
        "z_index": 2,
        "style": {"backgroundColor": "#dbeafe"},
    }
    return {"objects": [center, *notes, *connectors]}


def _retrospective_template() -> dict[str, Any]:
    """Three columns: What went well / What to improve / Action items."""
    objects: list[dict[str, Any]] = []
    headers = [
        (50, "What Went Well", "#dcfce7"),
        (350, "What to Improve", "#fef3c7"),
        (650, "Action Items", "#dbeafe"),
    ]
    for x, title, bg in headers:
        objects.append({
            "id": str(uuid.uuid4()),
            "kind": "text",
            "content": title,
            "position_x": x,
            "position_y": 50,
            "width": 250,
            "height": 50,
            "z_index": 1,
            "style": {"backgroundColor": bg, "fontWeight": "bold"},
        })
        for i in range(3):
            objects.append({
                "id": str(uuid.uuid4()),
                "kind": "note",
                "content": "",
                "position_x": x,
                "position_y": 120 + i * 120,
                "width": 250,
                "height": 100,
                "z_index": 0,
                "style": {"backgroundColor": bg},
            })
    return {"objects": objects}


def _swot_template() -> dict[str, Any]:
    """2x2 grid: Strengths, Weaknesses, Opportunities, Threats."""
    objects: list[dict[str, Any]] = []
    quadrants = [
        (50, 50, "Strengths", "#dcfce7"),
        (350, 50, "Weaknesses", "#fecaca"),
        (50, 350, "Opportunities", "#dbeafe"),
        (350, 350, "Threats", "#fef3c7"),
    ]
    for x, y, title, bg in quadrants:
        objects.append({
            "id": str(uuid.uuid4()),
            "kind": "text",
            "content": title,
            "position_x": x,
            "position_y": y,
            "width": 280,
            "height": 40,
            "z_index": 1,
            "style": {"backgroundColor": bg, "fontWeight": "bold"},
        })
        objects.append({
            "id": str(uuid.uuid4()),
            "kind": "shape",
            "content": None,
            "position_x": x,
            "position_y": y + 50,
            "width": 280,
            "height": 230,
            "z_index": 0,
            "style": {"backgroundColor": bg, "opacity": 0.3},
        })
    return {"objects": objects}


def _mind_map_template() -> dict[str, Any]:
    """Central topic with 4 branching subtopic notes + connectors."""
    center_id = str(uuid.uuid4())
    objects: list[dict[str, Any]] = [{
        "id": center_id,
        "kind": "note",
        "content": "Main Topic",
        "position_x": 400,
        "position_y": 300,
        "width": 180,
        "height": 80,
        "z_index": 2,
        "style": {"backgroundColor": "#e0e7ff"},
    }]
    branches = [
        (-300, -180, "Subtopic 1", "#fef3c7"),
        (300, -180, "Subtopic 2", "#dcfce7"),
        (-300, 180, "Subtopic 3", "#fecaca"),
        (300, 180, "Subtopic 4", "#dbeafe"),
    ]
    for dx, dy, label, bg in branches:
        nid = str(uuid.uuid4())
        objects.append({
            "id": nid,
            "kind": "note",
            "content": label,
            "position_x": 400 + dx,
            "position_y": 300 + dy,
            "width": 160,
            "height": 80,
            "z_index": 1,
            "style": {"backgroundColor": bg},
        })
        objects.append({
            "id": str(uuid.uuid4()),
            "kind": "connector",
            "content": None,
            "position_x": 400,
            "position_y": 300,
            "width": abs(dx),
            "height": abs(dy),
            "z_index": 0,
            "style": {},
        })
    return {"objects": objects}


_PLATFORM_TEMPLATES = [
    {
        "name": "Brainstorming",
        "description": "Central topic with surrounding idea notes and connectors",
        "template_data": _brainstorming_template,
    },
    {
        "name": "Retrospective",
        "description": "Three columns: What went well, What to improve, Action items",
        "template_data": _retrospective_template,
    },
    {
        "name": "SWOT Analysis",
        "description": "2x2 grid of Strengths, Weaknesses, Opportunities, Threats",
        "template_data": _swot_template,
    },
    {
        "name": "Mind Map",
        "description": "Central topic with branching subtopics and connectors",
        "template_data": _mind_map_template,
    },
]


def upgrade() -> None:
    canvas_template_scope = pg.ENUM("platform", "project", name="canvas_template_scope", create_type=False)
    canvas_template_scope.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "canvas_templates",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "scope",
            pg.ENUM("platform", "project", name="canvas_template_scope", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("template_data", pg.JSONB, nullable=False),
        sa.Column(
            "created_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("scope", "project_id", "name", name="uq_canvas_templates_scope_project_name"),
    )

    op.create_index(
        "ix_canvas_templates_scope",
        "canvas_templates",
        ["scope"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_canvas_templates_project_id",
        "canvas_templates",
        ["project_id"],
        postgresql_where=sa.text("deleted_at IS NULL AND project_id IS NOT NULL"),
    )

    bind = op.get_bind()
    insert = sa.text(
        "INSERT INTO canvas_templates "
        "(id, scope, project_id, name, description, template_data, created_by_user_id) "
        "SELECT :id, CAST('platform' AS canvas_template_scope), NULL, :name, :description, "
        "CAST(:template_data AS jsonb), NULL "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM canvas_templates WHERE scope = 'platform' AND name = :name"
        ")"
    )

    for tmpl in _PLATFORM_TEMPLATES:
        data = tmpl["template_data"]()
        bind.execute(
            insert,
            {
                "id": str(uuid.uuid4()),
                "name": tmpl["name"],
                "description": tmpl["description"],
                "template_data": json.dumps(data),
            },
        )


def downgrade() -> None:
    op.drop_table("canvas_templates")
    op.execute("DROP TYPE IF EXISTS canvas_template_scope")
