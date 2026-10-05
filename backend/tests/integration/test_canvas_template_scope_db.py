"""The canvas-template list filter, executed against a real PostgreSQL.

Dossier ``docs/tasks/2026-10-05-canvas-template-tenant-scope`` (audit F-12).
`scope=project` with no `project_id` filtered by scope alone, so every
project's templates, across tenants, came back to any caller. The assertion
that matters is the confidentiality one: for every filter combination, no
returned project template belongs to a project other than the one asked for.

Marked `db`: it needs the provisioned datastore of the `backend-db` CI job.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.canvas.domain.models import CanvasTemplateScope
from contexts.canvas.infrastructure import tables as canvas_t
from contexts.canvas.infrastructure.repositories.canvas_template_repo import CanvasTemplateRepository
from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.infrastructure import tables as ten_t

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Scenario:
    org_id: uuid.UUID
    owner: uuid.UUID
    project_id: uuid.UUID
    foreign_project_id: uuid.UUID
    template_id: uuid.UUID
    foreign_template_id: uuid.UUID


@pytest.fixture
async def scenario(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[Scenario]:
    s = Scenario(
        org_id=uuid.uuid4(),
        owner=uuid.uuid4(),
        project_id=uuid.uuid4(),
        foreign_project_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        foreign_template_id=uuid.uuid4(),
    )
    async with sessionmaker() as session:
        await session.execute(
            users_t.insert().values(id=s.owner, email=f"tpl-{s.owner}@test.invalid", password_hash="x")
        )
        await session.execute(
            ten_t.orgs.insert().values(id=s.org_id, name="tpl-org", creator_user_id=s.owner)
        )
        for pid, name in ((s.project_id, "tpl-p1"), (s.foreign_project_id, "tpl-p2")):
            await session.execute(
                ten_t.projects.insert().values(
                    id=pid, name=name, owner_org_id=s.org_id, created_by_user_id=s.owner
                )
            )
        for tid, pid in ((s.template_id, s.project_id), (s.foreign_template_id, s.foreign_project_id)):
            await session.execute(
                canvas_t.canvas_templates.insert().values(
                    id=tid,
                    scope=CanvasTemplateScope.PROJECT.value,
                    project_id=pid,
                    name=f"tpl-{tid}",
                    template_data={"elements": []},
                    created_by_user_id=s.owner,
                )
            )
        await session.commit()

    try:
        yield s
    finally:
        async with sessionmaker() as cleanup:
            # The org cascades to its projects and their templates.
            await cleanup.execute(ten_t.orgs.delete().where(ten_t.orgs.c.id == s.org_id))
            await cleanup.execute(users_t.delete().where(users_t.c.id == s.owner))
            await cleanup.commit()


_SCOPES = (None, CanvasTemplateScope.PLATFORM, CanvasTemplateScope.PROJECT)


@pytest.mark.parametrize(("scope", "ask_for_project"), list(itertools.product(_SCOPES, (False, True))))
async def test_no_filter_returns_another_projects_template(
    sessionmaker: async_sessionmaker[AsyncSession],
    scenario: Scenario,
    scope: CanvasTemplateScope | None,
    ask_for_project: bool,
) -> None:
    project_id = scenario.project_id if ask_for_project else None
    async with sessionmaker() as session:
        rows = await CanvasTemplateRepository(session).list_templates(scope=scope, project_id=project_id)

    foreign = [t.id for t in rows if t.scope is CanvasTemplateScope.PROJECT and t.project_id != project_id]
    assert foreign == []


async def test_the_asked_for_project_still_gets_its_own_template(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        rows = await CanvasTemplateRepository(session).list_templates(
            scope=CanvasTemplateScope.PROJECT, project_id=scenario.project_id
        )

    assert [t.id for t in rows] == [scenario.template_id]
