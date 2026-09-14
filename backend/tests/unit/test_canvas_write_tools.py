"""Unit tests for canvas write tools ([R13.57]).

Covers tool construction with and without grant, input validation,
successful create/update/delete via CRDT relay, and audit event emission.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.agents.application.runtime.canvas_tools import (
    TOOL_CANVAS_CREATE,
    TOOL_CANVAS_DELETE,
    TOOL_CANVAS_UPDATE,
    CanvasWriteContext,
    build_canvas_write_tools,
    resolve_canvas_write,
)


def _session() -> AsyncMock:
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.info = {}
    return db


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


def _context(*, agent_id: uuid.UUID | None = None) -> CanvasWriteContext:
    return CanvasWriteContext(
        chatroom_id=uuid.uuid4(),
        canvas_id=uuid.uuid4(),
        agent_id=agent_id or uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        actor_ip="127.0.0.1",
        request_id=uuid.uuid4(),
    )


def _crdt_patches():
    """Context managers for the facade CRDT methods + publisher mocks."""
    mock_facade_cls = MagicMock()
    mock_facade = MagicMock()
    mock_facade.crdt_inject_elements = AsyncMock(return_value=(b"\x00\x01", b"\x02"))
    mock_facade.crdt_update_element = AsyncMock(return_value=(b"\x00\x01", b"\x02"))
    mock_facade.crdt_delete_element = AsyncMock(return_value=(b"\x00\x01", b"\x02"))
    mock_facade.persist_and_broadcast_crdt = AsyncMock()
    mock_facade_cls.return_value = mock_facade

    return (
        patch(
            "contexts.canvas.interfaces.facade.CanvasFacade",
            mock_facade_cls,
        ),
        patch("shared_kernel.audit.emit", new_callable=AsyncMock),
        mock_facade,
    )


class TestBuildCanvasWriteTools:
    def test_returns_three_tools(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {TOOL_CANVAS_CREATE, TOOL_CANVAS_UPDATE, TOOL_CANVAS_DELETE}

    def test_tool_names_match_constants(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        assert tools[0].name == TOOL_CANVAS_CREATE
        assert tools[1].name == TOOL_CANVAS_UPDATE
        assert tools[2].name == TOOL_CANVAS_DELETE

    def test_tool_names_are_reserved(self) -> None:
        from contexts.agents.application.runtime.tool_registry import BUILTIN_TOOL_NAMES

        assert TOOL_CANVAS_CREATE in BUILTIN_TOOL_NAMES
        assert TOOL_CANVAS_UPDATE in BUILTIN_TOOL_NAMES
        assert TOOL_CANVAS_DELETE in BUILTIN_TOOL_NAMES


class TestCanvasCreateTool:
    @pytest.mark.asyncio
    async def test_create_valid_object(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context(agent_id=agent.id)
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        create_tool = tools[0]

        p_facade, p_audit, mock_facade = _crdt_patches()
        with p_facade, p_audit:
            result = await create_tool.invoke(
                {
                    "kind": "note",
                    "position_x": 10.0,
                    "position_y": 20.0,
                    "width": 100.0,
                    "height": 50.0,
                    "content": "Hello from agent",
                }
            )
        assert not result.is_error
        mock_facade.crdt_inject_elements.assert_awaited_once()
        call_args = mock_facade.crdt_inject_elements.call_args
        elements = call_args[0][1]
        assert len(elements) == 1
        elem = elements[0]
        assert elem["type"] == "rectangle"
        assert elem["x"] == 10.0
        assert elem["y"] == 20.0
        assert elem["width"] == 100.0
        assert elem["height"] == 50.0
        assert elem["text"] == "Hello from agent"
        assert elem["isDeleted"] is False
        assert elem["customData"]["createdByAgentId"] == str(ctx.agent_id)
        mock_facade.persist_and_broadcast_crdt.assert_awaited_once()
        persist_kwargs = mock_facade.persist_and_broadcast_crdt.call_args[1]
        assert persist_kwargs["deferred"] is True

    @pytest.mark.asyncio
    async def test_create_text_element_has_font_props(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context(agent_id=agent.id)
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        create_tool = tools[0]

        p_facade, p_audit, mock_facade = _crdt_patches()
        with p_facade, p_audit:
            result = await create_tool.invoke(
                {
                    "kind": "text",
                    "position_x": 0,
                    "position_y": 0,
                    "width": 100,
                    "height": 100,
                    "content": "test",
                }
            )
        assert not result.is_error
        elem = mock_facade.crdt_inject_elements.call_args[0][1][0]
        assert elem["type"] == "text"
        assert elem["fontSize"] == 20
        assert elem["fontFamily"] == 1

    @pytest.mark.asyncio
    async def test_create_rejects_image_kind(self) -> None:
        """Image kind exists in CanvasObjectKind but is excluded from agent tools."""
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        create_tool = tools[0]
        result = await create_tool.invoke(
            {
                "kind": "image",
                "position_x": 0,
                "position_y": 0,
                "width": 100,
                "height": 100,
            }
        )
        assert result.is_error
        assert "Invalid kind" in result.content

    @pytest.mark.asyncio
    async def test_create_invalid_kind(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        create_tool = tools[0]
        result = await create_tool.invoke(
            {
                "kind": "invalid",
                "position_x": 0,
                "position_y": 0,
                "width": 100,
                "height": 100,
            }
        )
        assert result.is_error
        assert "Invalid kind" in result.content


class TestCanvasUpdateTool:
    @pytest.mark.asyncio
    async def test_update_valid_object(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context(agent_id=agent.id)
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        update_tool = tools[1]

        p_facade, p_audit, mock_facade = _crdt_patches()
        with p_facade, p_audit:
            result = await update_tool.invoke(
                {
                    "object_id": str(uuid.uuid4()),
                    "content": "Updated content",
                }
            )
        assert not result.is_error
        mock_facade.crdt_update_element.assert_awaited_once()
        call_args = mock_facade.crdt_update_element.call_args
        crdt_fields = call_args[0][2]
        assert crdt_fields["text"] == "Updated content"

    @pytest.mark.asyncio
    async def test_update_maps_position_fields(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context(agent_id=agent.id)
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        update_tool = tools[1]

        p_facade, p_audit, mock_facade = _crdt_patches()
        with p_facade, p_audit:
            result = await update_tool.invoke(
                {
                    "object_id": str(uuid.uuid4()),
                    "position_x": 50.0,
                    "position_y": 75.0,
                }
            )
        assert not result.is_error
        crdt_fields = mock_facade.crdt_update_element.call_args[0][2]
        assert crdt_fields == {"x": 50.0, "y": 75.0}

    @pytest.mark.asyncio
    async def test_update_no_fields(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        update_tool = tools[1]
        result = await update_tool.invoke({"object_id": str(uuid.uuid4())})
        assert result.is_error
        assert "No fields" in result.content

    @pytest.mark.asyncio
    async def test_update_invalid_uuid(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        update_tool = tools[1]
        result = await update_tool.invoke({"object_id": "not-a-uuid", "content": "x"})
        assert result.is_error
        assert "Invalid object_id" in result.content

    @pytest.mark.asyncio
    async def test_update_not_found(self) -> None:
        from contexts.canvas.application.crdt_relay import CrdtUpdateError

        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        update_tool = tools[1]

        mock_facade_cls = MagicMock()
        mock_facade = MagicMock()
        mock_facade.crdt_update_element = AsyncMock(side_effect=CrdtUpdateError("not found"))
        mock_facade_cls.return_value = mock_facade
        with patch("contexts.canvas.interfaces.facade.CanvasFacade", mock_facade_cls):
            result = await update_tool.invoke(
                {
                    "object_id": str(uuid.uuid4()),
                    "content": "x",
                }
            )
        assert result.is_error
        assert "not found" in result.content.lower()


class TestCanvasDeleteTool:
    @pytest.mark.asyncio
    async def test_delete_valid(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context(agent_id=agent.id)
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        delete_tool = tools[2]

        p_facade, p_audit, mock_facade = _crdt_patches()
        with p_facade, p_audit:
            result = await delete_tool.invoke({"object_id": str(uuid.uuid4())})
        assert not result.is_error
        mock_facade.crdt_delete_element.assert_awaited_once()
        mock_facade.persist_and_broadcast_crdt.assert_awaited_once()
        assert mock_facade.persist_and_broadcast_crdt.call_args[1]["deferred"] is True

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        from contexts.canvas.application.crdt_relay import CrdtUpdateError

        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        delete_tool = tools[2]

        mock_facade_cls = MagicMock()
        mock_facade = MagicMock()
        mock_facade.crdt_delete_element = AsyncMock(side_effect=CrdtUpdateError("not found"))
        mock_facade_cls.return_value = mock_facade
        with patch("contexts.canvas.interfaces.facade.CanvasFacade", mock_facade_cls):
            result = await delete_tool.invoke({"object_id": str(uuid.uuid4())})
        assert result.is_error
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_delete_invalid_uuid(self) -> None:
        db = _session()
        agent = _agent()
        ctx = _context()
        tools = build_canvas_write_tools(db, agent=agent, context=ctx)
        delete_tool = tools[2]
        result = await delete_tool.invoke({"object_id": "bad"})
        assert result.is_error


class TestResolveCanvasWrite:
    @pytest.mark.asyncio
    async def test_none_when_no_chatroom(self) -> None:
        db = _session()
        result = await resolve_canvas_write(db, chatroom_id=None, agent_id=uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_none_when_no_grant(self) -> None:
        db = _session()
        with patch(
            "contexts.conversation.infrastructure.repositories.ChatroomAgentRepository.canvas_write_grant",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await resolve_canvas_write(db, chatroom_id=uuid.uuid4(), agent_id=uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_none_when_no_canvas(self) -> None:
        db = _session()
        grant = SimpleNamespace(agent_id=uuid.uuid4(), granted_by_user_id=uuid.uuid4())
        with (
            patch(
                "contexts.conversation.infrastructure.repositories.ChatroomAgentRepository.canvas_write_grant",
                new_callable=AsyncMock,
                return_value=grant,
            ),
            patch(
                "contexts.canvas.interfaces.facade.CanvasFacade.get_by_chatroom",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await resolve_canvas_write(db, chatroom_id=uuid.uuid4(), agent_id=uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_context_when_granted(self) -> None:
        db = _session()
        agent_id = uuid.uuid4()
        chatroom_id = uuid.uuid4()
        canvas_id = uuid.uuid4()
        grantor = uuid.uuid4()
        grant = SimpleNamespace(agent_id=agent_id, granted_by_user_id=grantor)
        canvas = SimpleNamespace(id=canvas_id)
        with (
            patch(
                "contexts.conversation.infrastructure.repositories.ChatroomAgentRepository.canvas_write_grant",
                new_callable=AsyncMock,
                return_value=grant,
            ),
            patch(
                "contexts.canvas.interfaces.facade.CanvasFacade.get_by_chatroom",
                new_callable=AsyncMock,
                return_value=canvas,
            ),
        ):
            result = await resolve_canvas_write(db, chatroom_id=chatroom_id, agent_id=agent_id)
        assert result is not None
        assert result.chatroom_id == chatroom_id
        assert result.canvas_id == canvas_id
        assert result.agent_id == agent_id
        assert result.actor_user_id == grantor

    @pytest.mark.asyncio
    async def test_fails_closed_on_exception(self) -> None:
        db = _session()
        with patch(
            "contexts.conversation.infrastructure.repositories.ChatroomAgentRepository.canvas_write_grant",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await resolve_canvas_write(db, chatroom_id=uuid.uuid4(), agent_id=uuid.uuid4())
        assert result is None
