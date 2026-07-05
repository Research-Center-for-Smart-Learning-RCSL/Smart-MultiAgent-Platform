"""SQLAlchemy Core repositories for the prompt_studio context.

Mirror the agents context: infrastructure holds the queries directly, rows map
to frozen domain models via module-level ``_row_to_*`` functions, and
``version`` is bumped by the ``smap_bump_version`` DB trigger — never in code.
Repositories never commit; the ``db_session`` dependency owns the transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.prompt_studio.domain.errors import (
    AssistantConfigNotFound,
    TemplateNotFound,
    VersionMismatch,
)
from contexts.prompt_studio.domain.models import (
    AssistantConfig,
    AssistantFile,
    PromptScope,
    PromptTemplate,
    ScanStatus,
)
from contexts.prompt_studio.infrastructure import tables as t


def _row_to_config(row: Any) -> AssistantConfig:
    return AssistantConfig(
        id=row.id,
        scope=PromptScope(row.scope),
        org_id=row.org_id,
        user_id=row.user_id,
        system_prompt=row.system_prompt,
        key_id=row.key_id,
        model_id=row.model_id,
        daily_request_limit_per_user=row.daily_request_limit_per_user,
        enabled=row.enabled,
        hide_platform_templates=row.hide_platform_templates,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_file(row: Any) -> AssistantFile:
    return AssistantFile(
        id=row.id,
        config_id=row.config_id,
        filename=row.filename,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        mime=row.mime,
        minio_key=row.minio_key,
        scan_status=ScanStatus(row.scan_status),
        extracted_chars=row.extracted_chars,
        created_at=row.created_at,
    )


def _row_to_template(row: Any) -> PromptTemplate:
    return PromptTemplate(
        id=row.id,
        scope=PromptScope(row.scope),
        org_id=row.org_id,
        user_id=row.user_id,
        name=row.name,
        description=row.description,
        body=row.body,
        position=row.position,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AssistantConfigRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, config_id: uuid.UUID) -> AssistantConfig | None:
        row = (
            await self._db.execute(
                t.prompt_assistant_configs.select().where(t.prompt_assistant_configs.c.id == config_id)
            )
        ).first()
        return _row_to_config(row) if row else None

    async def get_by_scope(
        self,
        scope: PromptScope,
        *,
        org_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> AssistantConfig | None:
        c = t.prompt_assistant_configs.c
        pred = c.scope == scope.value
        if scope is PromptScope.ORG:
            pred = sa.and_(pred, c.org_id == org_id)
        elif scope is PromptScope.USER:
            pred = sa.and_(pred, c.user_id == user_id)
        row = (await self._db.execute(t.prompt_assistant_configs.select().where(pred))).first()
        return _row_to_config(row) if row else None

    async def create(
        self,
        *,
        scope: PromptScope,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        values: dict[str, Any],
    ) -> AssistantConfig:
        row = (
            await self._db.execute(
                t.prompt_assistant_configs.insert()
                .values(scope=scope.value, org_id=org_id, user_id=user_id, **values)
                .returning(t.prompt_assistant_configs)
            )
        ).one()
        return _row_to_config(row)

    async def update(
        self, config_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
    ) -> AssistantConfig:
        c = t.prompt_assistant_configs.c
        stmt = (
            t.prompt_assistant_configs.update()
            .where(sa.and_(c.id == config_id, c.version == expected_version))
            .values(**values)
            .returning(t.prompt_assistant_configs)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            if await self.get_by_id(config_id) is None:
                raise AssistantConfigNotFound(str(config_id))
            raise VersionMismatch(str(config_id))
        return _row_to_config(row)

    # -- reference files -----------------------------------------------------

    async def list_files(self, config_id: uuid.UUID) -> list[AssistantFile]:
        rows = (
            await self._db.execute(
                t.prompt_assistant_files.select()
                .where(t.prompt_assistant_files.c.config_id == config_id)
                .order_by(t.prompt_assistant_files.c.created_at)
            )
        ).all()
        return [_row_to_file(r) for r in rows]

    async def get_file(self, config_id: uuid.UUID, file_id: uuid.UUID) -> AssistantFile | None:
        c = t.prompt_assistant_files.c
        row = (
            await self._db.execute(
                t.prompt_assistant_files.select().where(sa.and_(c.id == file_id, c.config_id == config_id))
            )
        ).first()
        return _row_to_file(row) if row else None

    async def add_file(self, *, config_id: uuid.UUID, values: dict[str, Any]) -> AssistantFile:
        row = (
            await self._db.execute(
                t.prompt_assistant_files.insert()
                .values(config_id=config_id, **values)
                .returning(t.prompt_assistant_files)
            )
        ).one()
        return _row_to_file(row)

    async def set_file_scan_result(
        self, file_id: uuid.UUID, *, scan_status: ScanStatus, extracted_chars: int
    ) -> None:
        await self._db.execute(
            t.prompt_assistant_files.update()
            .where(t.prompt_assistant_files.c.id == file_id)
            .values(scan_status=scan_status.value, extracted_chars=extracted_chars)
        )

    async def delete_file(self, config_id: uuid.UUID, file_id: uuid.UUID) -> bool:
        c = t.prompt_assistant_files.c
        result = await self._db.execute(
            t.prompt_assistant_files.delete().where(sa.and_(c.id == file_id, c.config_id == config_id))
        )
        return bool(result.rowcount)

    async def sum_extracted_chars(self, config_id: uuid.UUID, *, only_clean: bool = True) -> int:
        c = t.prompt_assistant_files.c
        pred = c.config_id == config_id
        if only_clean:
            pred = sa.and_(pred, c.scan_status == ScanStatus.CLEAN.value)
        total = (
            await self._db.execute(sa.select(sa.func.coalesce(sa.func.sum(c.extracted_chars), 0)).where(pred))
        ).scalar_one()
        return int(total)


class PromptTemplateRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, template_id: uuid.UUID) -> PromptTemplate | None:
        row = (
            await self._db.execute(t.prompt_templates.select().where(t.prompt_templates.c.id == template_id))
        ).first()
        return _row_to_template(row) if row else None

    async def create(
        self,
        *,
        scope: PromptScope,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        position: int,
    ) -> PromptTemplate:
        row = (
            await self._db.execute(
                t.prompt_templates.insert()
                .values(
                    scope=scope.value,
                    org_id=org_id,
                    user_id=user_id,
                    name=name,
                    description=description,
                    body=body,
                    position=position,
                )
                .returning(t.prompt_templates)
            )
        ).one()
        return _row_to_template(row)

    async def update(
        self, template_id: uuid.UUID, *, expected_version: int, values: dict[str, Any]
    ) -> PromptTemplate:
        c = t.prompt_templates.c
        stmt = (
            t.prompt_templates.update()
            .where(sa.and_(c.id == template_id, c.version == expected_version))
            .values(**values)
            .returning(t.prompt_templates)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            if await self.get(template_id) is None:
                raise TemplateNotFound(str(template_id))
            raise VersionMismatch(str(template_id))
        return _row_to_template(row)

    async def delete(self, template_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.prompt_templates.delete().where(t.prompt_templates.c.id == template_id)
        )
        return bool(result.rowcount)

    async def count_for_scope(
        self, scope: PromptScope, *, org_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
    ) -> int:
        pred = self._scope_pred(scope, org_id=org_id, user_id=user_id)
        return int(
            (
                await self._db.execute(sa.select(sa.func.count()).select_from(t.prompt_templates).where(pred))
            ).scalar_one()
        )

    async def list_for_scope(
        self, scope: PromptScope, *, org_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
    ) -> list[PromptTemplate]:
        c = t.prompt_templates.c
        pred = self._scope_pred(scope, org_id=org_id, user_id=user_id)
        rows = (
            await self._db.execute(t.prompt_templates.select().where(pred).order_by(c.position, c.created_at))
        ).all()
        return [_row_to_template(r) for r in rows]

    @staticmethod
    def _scope_pred(
        scope: PromptScope, *, org_id: uuid.UUID | None, user_id: uuid.UUID | None
    ) -> sa.ColumnElement[bool]:
        c = t.prompt_templates.c
        pred: sa.ColumnElement[bool] = c.scope == scope.value
        if scope is PromptScope.ORG:
            pred = sa.and_(pred, c.org_id == org_id)
        elif scope is PromptScope.USER:
            pred = sa.and_(pred, c.user_id == user_id)
        return pred


__all__ = ["AssistantConfigRepository", "PromptTemplateRepository"]
