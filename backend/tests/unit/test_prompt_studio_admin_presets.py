"""`/api/admin/prompt-assistant/presets` — platform preset CRUD (§29 / R29.16, AC-6).

Route handlers are called directly (project convention, see
test_admin_activities_routes.py) with `ConfigService`/`FileService` methods
patched, so the request/response mapping in the route module runs for real
while the DB and Vault-backed key checks stay out of scope.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1 import prompt_studio
from app.api.v1.admin_deps import require_admin
from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.application.file_service import FileService
from contexts.prompt_studio.domain.models import AssistantConfig, PromptScope

_NOW = datetime(2026, 9, 6, tzinfo=UTC)
_ADMIN = SimpleNamespace(user_id=uuid.uuid4(), is_admin=True)
_CTX = SimpleNamespace(actor_ip=None, request_id=None)


def _preset(*, enabled: bool = False, name: str = "A") -> AssistantConfig:
    return AssistantConfig(
        id=uuid.uuid4(),
        scope=PromptScope.PLATFORM,
        org_id=None,
        user_id=None,
        persona_prompt="persona",
        name=name,
        description="desc",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=enabled,
        hide_platform_templates=False,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _preset_in(**over: object) -> prompt_studio.AssistantConfigPresetPutIn:
    base: dict = {
        "name": "A",
        "description": "desc",
        "persona_prompt": "persona",
        "system_prompt": "",
        "key_id": None,
        "model_id": None,
        "daily_request_limit_per_user": 50,
        "enabled": False,
    }
    base.update(over)
    return prompt_studio.AssistantConfigPresetPutIn(**base)


@pytest.mark.parametrize(
    "route",
    [
        prompt_studio.admin_list_presets,
        prompt_studio.admin_create_preset,
        prompt_studio.admin_update_preset,
        prompt_studio.admin_delete_preset,
        prompt_studio.admin_upload_preset_file,
        prompt_studio.admin_delete_preset_file,
    ],
    ids=["list", "create", "update", "delete", "upload-file", "delete-file"],
)
def test_every_preset_route_depends_on_require_admin(route: object) -> None:
    params = inspect.signature(route).parameters  # type: ignore[arg-type]
    deps = [p.default.dependency for p in params.values() if hasattr(p.default, "dependency")]
    assert require_admin in deps


class TestAdminListPresets:
    async def test_lists_all_platform_presets(self) -> None:
        presets = [_preset(name="A"), _preset(name="B", enabled=True)]
        with (
            patch.object(ConfigService, "list_platform_presets", AsyncMock(return_value=presets)),
            patch.object(ConfigService, "list_files", AsyncMock(return_value=[])),
        ):
            out = await prompt_studio.admin_list_presets(_=_ADMIN, db=MagicMock())

        assert [p.name for p in out] == ["A", "B"]
        assert [p.enabled for p in out] == [False, True]


class TestAdminCreatePreset:
    async def test_creates_and_returns_preset(self) -> None:
        created = _preset(name="New")
        with (
            patch.object(ConfigService, "create_preset", AsyncMock(return_value=created)) as create,
            patch.object(ConfigService, "list_files", AsyncMock(return_value=[])),
        ):
            out = await prompt_studio.admin_create_preset(
                body=_preset_in(name="New"), ctx=_CTX, principal=_ADMIN, db=MagicMock()
            )

        assert out.name == "New"
        assert create.call_args.kwargs["actor_user_id"] == _ADMIN.user_id
        assert create.call_args.kwargs["persona_prompt"] == "persona"


class TestAdminUpdatePreset:
    async def test_parses_if_match_and_forwards_expected_version(self) -> None:
        updated = _preset(name="Updated")
        with (
            patch.object(ConfigService, "update_preset", AsyncMock(return_value=updated)) as update,
            patch.object(ConfigService, "list_files", AsyncMock(return_value=[])),
        ):
            out = await prompt_studio.admin_update_preset(
                body=_preset_in(name="Updated"),
                config_id=updated.id,
                if_match="3",
                ctx=_CTX,
                principal=_ADMIN,
                db=MagicMock(),
            )

        assert out.name == "Updated"
        assert update.call_args.kwargs["expected_version"] == 3
        assert update.call_args.kwargs["preset_id"] == updated.id


class TestAdminDeletePreset:
    async def test_delegates_to_delete_preset(self) -> None:
        preset_id = uuid.uuid4()
        with patch.object(ConfigService, "delete_preset", AsyncMock(return_value=None)) as delete:
            await prompt_studio.admin_delete_preset(
                config_id=preset_id, ctx=_CTX, principal=_ADMIN, db=MagicMock()
            )

        assert delete.call_args.kwargs["preset_id"] == preset_id


class TestAdminPresetFiles:
    async def test_upload_checks_preset_exists_then_delegates_to_file_service(self) -> None:
        preset = _preset()
        upload_file = SimpleNamespace(
            filename="a.txt",
            content_type="text/plain",
            size=10,
            read=AsyncMock(return_value=b"hello"),
        )
        file_out = SimpleNamespace(
            id=uuid.uuid4(),
            filename="a.txt",
            size_bytes=5,
            sha256="x",
            mime="text/plain",
            minio_key="k",
            scan_status=SimpleNamespace(value="clean"),
            extracted_chars=5,
            extracted_text=None,
            created_at=_NOW,
        )
        with (
            patch.object(ConfigService, "get_platform_preset_or_raise", AsyncMock(return_value=preset)),
            patch.object(FileService, "upload_reference_file", AsyncMock(return_value=file_out)),
        ):
            out = await prompt_studio.admin_upload_preset_file(
                config_id=preset.id, file=upload_file, ctx=_CTX, principal=_ADMIN, db=MagicMock()
            )

        assert out.filename == "a.txt"

    async def test_delete_file_checks_preset_exists_then_delegates_to_file_service(self) -> None:
        preset = _preset()
        file_id = uuid.uuid4()
        with (
            patch.object(ConfigService, "get_platform_preset_or_raise", AsyncMock(return_value=preset)),
            patch.object(FileService, "remove_reference_file", AsyncMock(return_value=None)) as remove,
        ):
            await prompt_studio.admin_delete_preset_file(
                config_id=preset.id, file_id=file_id, ctx=_CTX, principal=_ADMIN, db=MagicMock()
            )

        assert remove.call_args.kwargs["config_id"] == preset.id
        assert remove.call_args.kwargs["file_id"] == file_id
