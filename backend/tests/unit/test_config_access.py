"""F-2: owner-aware read/subscribe ACL for knowledge configs (R11.17).

``has_config_read_access`` is the single predicate the GraphRAG REST reads, the
config-scoped WS handshake, and the F-25 mid-socket re-auth all delegate to, so
these tests are the authoritative coverage for the room-ACL / concept_map_enabled
gate at every surface.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from contexts.knowledge.interfaces.config_access import has_config_read_access
from shared_kernel.auth.permissions import Principal

_MOD = "contexts.knowledge.interfaces.config_access"


def _principal(*, is_admin: bool = False) -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)


def _chatroom_cfg() -> SimpleNamespace:
    return SimpleNamespace(owner_kind="chatroom", owner_chatroom_id=uuid.uuid4(), project_id=uuid.uuid4())


def _agent_group_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        owner_kind="agent_group", owner_agent_group_id=uuid.uuid4(), project_id=uuid.uuid4()
    )


def _workspace_cfg() -> SimpleNamespace:
    return SimpleNamespace(owner_kind="workspace", owner_workspace_id=uuid.uuid4(), project_id=uuid.uuid4())


def _rag_cfg() -> SimpleNamespace:
    # RAG / Knowledge-Map configs carry no owner_kind.
    return SimpleNamespace(project_id=uuid.uuid4())


def _resolver(*, has_role: bool):
    resolver = MagicMock()
    resolver.roles_for = AsyncMock(return_value=({"project_member"} if has_role else set()))
    return MagicMock(return_value=resolver)


class TestChatroomOwned:
    async def test_room_permitted_member_allowed(self) -> None:
        with (
            patch(f"{_MOD}.resolve_room_access", new=AsyncMock(return_value=MagicMock())),
            patch(f"{_MOD}.ensure_can_read", new=MagicMock(return_value=None)),
        ):
            assert await has_config_read_access(MagicMock(), principal=_principal(), cfg=_chatroom_cfg())

    async def test_room_denied_member_refused(self) -> None:
        def _raise(_access, *, is_admin):
            raise ForbiddenInRoom("nope")

        with (
            patch(f"{_MOD}.resolve_room_access", new=AsyncMock(return_value=MagicMock())),
            patch(f"{_MOD}.ensure_can_read", new=MagicMock(side_effect=_raise)),
        ):
            assert not await has_config_read_access(MagicMock(), principal=_principal(), cfg=_chatroom_cfg())

    async def test_deleted_room_refused_no_fallthrough(self) -> None:
        # A missing room must deny, never fall through to a project check.
        with patch(
            f"{_MOD}.resolve_room_access",
            new=AsyncMock(side_effect=ChatroomNotFound("gone")),
        ):
            assert not await has_config_read_access(MagicMock(), principal=_principal(), cfg=_chatroom_cfg())


class TestAgentGroupOwned:
    async def test_member_with_enabled_allowed(self) -> None:
        facade = MagicMock(get_group=AsyncMock(return_value=SimpleNamespace(concept_map_enabled=True)))
        with (
            patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=True)),
            patch(f"{_MOD}.AgentGroupFacade", new=MagicMock(return_value=facade)),
        ):
            assert await has_config_read_access(MagicMock(), principal=_principal(), cfg=_agent_group_cfg())

    async def test_member_with_disabled_refused(self) -> None:
        # R11.17: without the concept_map_enabled opt-in, a project member is denied.
        facade = MagicMock(get_group=AsyncMock(return_value=SimpleNamespace(concept_map_enabled=False)))
        with (
            patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=True)),
            patch(f"{_MOD}.AgentGroupFacade", new=MagicMock(return_value=facade)),
        ):
            assert not await has_config_read_access(
                MagicMock(), principal=_principal(), cfg=_agent_group_cfg()
            )

    async def test_non_member_refused_before_enablement(self) -> None:
        with patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=False)):
            assert not await has_config_read_access(
                MagicMock(), principal=_principal(), cfg=_agent_group_cfg()
            )


class TestWorkspaceOwned:
    async def test_member_with_enabled_allowed(self) -> None:
        facade = MagicMock(get_workspace=AsyncMock(return_value=SimpleNamespace(concept_map_enabled=True)))
        with (
            patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=True)),
            patch(f"{_MOD}.ConversationFacade", new=MagicMock(return_value=facade)),
        ):
            assert await has_config_read_access(MagicMock(), principal=_principal(), cfg=_workspace_cfg())

    async def test_member_with_disabled_refused(self) -> None:
        facade = MagicMock(get_workspace=AsyncMock(return_value=SimpleNamespace(concept_map_enabled=False)))
        with (
            patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=True)),
            patch(f"{_MOD}.ConversationFacade", new=MagicMock(return_value=facade)),
        ):
            assert not await has_config_read_access(MagicMock(), principal=_principal(), cfg=_workspace_cfg())


class TestNonConceptMapConfigs:
    async def test_rag_config_member_allowed(self) -> None:
        with patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=True)):
            assert await has_config_read_access(MagicMock(), principal=_principal(), cfg=_rag_cfg())

    async def test_rag_config_non_member_refused(self) -> None:
        with patch(f"{_MOD}.TenancyRoleResolver", new=_resolver(has_role=False)):
            assert not await has_config_read_access(MagicMock(), principal=_principal(), cfg=_rag_cfg())


class TestAdminBypass:
    async def test_admin_allowed_for_room_denied_config(self) -> None:
        # Admin bypasses without touching the room ACL or role resolver.
        with (
            patch(f"{_MOD}.resolve_room_access", new=AsyncMock(side_effect=AssertionError)),
            patch(f"{_MOD}.TenancyRoleResolver", new=MagicMock(side_effect=AssertionError)),
        ):
            assert await has_config_read_access(
                MagicMock(), principal=_principal(is_admin=True), cfg=_chatroom_cfg()
            )
