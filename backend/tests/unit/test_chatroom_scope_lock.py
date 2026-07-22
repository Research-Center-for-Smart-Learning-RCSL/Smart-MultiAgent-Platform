"""Chatroom scope locking contracts for cross-context publishers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from contexts.conversation.infrastructure.repositories import ChatroomRepository


async def test_lock_live_project_id_locks_live_room_and_workspace_until_transaction_end() -> None:
    """A concurrent soft delete must wait while the caller publishes its event."""
    project_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(first=lambda: SimpleNamespace(project_id=project_id)))

    resolved = await ChatroomRepository(db).lock_live_project_id(uuid.uuid4())

    assert resolved == project_id
    statement = db.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR SHARE OF chatrooms, workspaces" in compiled
    assert "chatrooms.deleted_at IS NULL" in compiled
    assert "workspaces.deleted_at IS NULL" in compiled
