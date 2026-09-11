"""Canvas template domain model and service unit tests ([R13.59]-[R13.61])."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.canvas.application.template_service import (
    CanvasNotEmpty,
    CanvasTemplateService,
    TemplateDataTooLarge,
    TooManyTemplateObjects,
)
from contexts.canvas.domain.models import (
    CanvasObject,
    CanvasObjectKind,
    CanvasTemplate,
    CanvasTemplateScope,
)

# ---- helpers ----------------------------------------------------------------

_NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)


def _make_template(
    *,
    scope: CanvasTemplateScope = CanvasTemplateScope.PLATFORM,
    project_id: uuid.UUID | None = None,
    template_data: dict | None = None,
) -> CanvasTemplate:
    return CanvasTemplate(
        id=uuid.uuid4(),
        scope=scope,
        project_id=project_id,
        name="Test Template",
        description="A test",
        template_data=template_data or {"objects": []},
        created_by_user_id=None,
        created_at=_NOW,
    )


def _valid_object(**overrides: object) -> dict:
    base: dict = {
        "kind": "note",
        "position_x": 0,
        "position_y": 0,
        "width": 100,
        "height": 100,
    }
    base.update(overrides)
    return base


def _make_canvas_object(
    *,
    canvas_id: uuid.UUID | None = None,
    kind: CanvasObjectKind = CanvasObjectKind.NOTE,
    content: str | None = "hello",
) -> CanvasObject:
    return CanvasObject(
        id=uuid.uuid4(),
        canvas_id=canvas_id or uuid.uuid4(),
        kind=kind,
        position_x=10.0,
        position_y=20.0,
        width=160.0,
        height=100.0,
        z_index=1,
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _service() -> CanvasTemplateService:
    return CanvasTemplateService(AsyncMock())


# ---- domain model -----------------------------------------------------------


class TestCanvasTemplateScopeEnum:
    def test_platform_value(self) -> None:
        assert CanvasTemplateScope.PLATFORM.value == "platform"

    def test_project_value(self) -> None:
        assert CanvasTemplateScope.PROJECT.value == "project"

    def test_from_string(self) -> None:
        assert CanvasTemplateScope("platform") is CanvasTemplateScope.PLATFORM
        assert CanvasTemplateScope("project") is CanvasTemplateScope.PROJECT


class TestCanvasTemplateDataclass:
    def test_create_platform_template(self) -> None:
        t = _make_template(scope=CanvasTemplateScope.PLATFORM)
        assert t.scope is CanvasTemplateScope.PLATFORM
        assert t.project_id is None
        assert t.deleted_at is None

    def test_create_project_template(self) -> None:
        pid = uuid.uuid4()
        t = _make_template(scope=CanvasTemplateScope.PROJECT, project_id=pid)
        assert t.scope is CanvasTemplateScope.PROJECT
        assert t.project_id == pid

    def test_frozen(self) -> None:
        t = _make_template()
        with pytest.raises(AttributeError):
            t.name = "changed"  # type: ignore[misc]


# ---- validation -------------------------------------------------------------


class TestValidateTemplateData:
    def test_accepts_valid_data(self) -> None:
        svc = _service()
        svc._validate_template_data({"objects": [_valid_object()]})

    def test_accepts_empty_objects(self) -> None:
        svc = _service()
        svc._validate_template_data({"objects": []})

    def test_rejects_non_list_objects(self) -> None:
        svc = _service()
        with pytest.raises(ValueError, match="must be a list"):
            svc._validate_template_data({"objects": "not a list"})

    def test_rejects_too_many_objects(self) -> None:
        svc = _service()
        data = {"objects": [_valid_object() for _ in range(201)]}
        with pytest.raises(TooManyTemplateObjects):
            svc._validate_template_data(data)

    def test_accepts_exactly_200_objects(self) -> None:
        svc = _service()
        data = {"objects": [_valid_object() for _ in range(200)]}
        svc._validate_template_data(data)

    def test_rejects_missing_required_fields(self) -> None:
        svc = _service()
        incomplete = {"kind": "note", "position_x": 0}
        with pytest.raises(ValueError, match="missing required fields"):
            svc._validate_template_data({"objects": [incomplete]})

    def test_rejects_non_dict_object(self) -> None:
        svc = _service()
        with pytest.raises(ValueError, match="must be an object"):
            svc._validate_template_data({"objects": ["not a dict"]})

    def test_rejects_oversized_data(self) -> None:
        svc = _service()
        big_content = "x" * (600 * 1024)
        data = {"objects": [_valid_object(content=big_content)]}
        with pytest.raises(TemplateDataTooLarge):
            svc._validate_template_data(data)


# ---- apply_template ---------------------------------------------------------


class TestApplyTemplate:
    async def test_raises_canvas_not_empty(self) -> None:
        svc = _service()
        template = _make_template(
            template_data={"objects": [_valid_object()]},
        )
        svc._template_repo = MagicMock()
        svc._template_repo.get = AsyncMock(return_value=template)
        svc._canvas_repo = MagicMock()
        svc._canvas_repo.count_objects = AsyncMock(return_value=3)

        with pytest.raises(CanvasNotEmpty):
            await svc.apply_template(
                canvas_id=uuid.uuid4(),
                chatroom_id=uuid.uuid4(),
                template_id=template.id,
            )

    async def test_raises_value_error_for_missing_template(self) -> None:
        svc = _service()
        svc._template_repo = MagicMock()
        svc._template_repo.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Template not found"):
            await svc.apply_template(
                canvas_id=uuid.uuid4(),
                chatroom_id=uuid.uuid4(),
                template_id=uuid.uuid4(),
            )

    @patch("contexts.canvas.application.template_service.audit")
    @patch("contexts.canvas.application.canvas_service.CanvasService.batch_operate")
    async def test_creates_objects_via_batch_operate(
        self, mock_batch: AsyncMock, mock_audit: MagicMock
    ) -> None:
        mock_audit.emit = AsyncMock()
        svc = _service()
        obj_data = _valid_object(content="Idea 1", z_index=1, style={"bg": "red"})
        template = _make_template(template_data={"objects": [obj_data]})

        svc._template_repo = MagicMock()
        svc._template_repo.get = AsyncMock(return_value=template)
        svc._canvas_repo = MagicMock()
        svc._canvas_repo.count_objects = AsyncMock(return_value=0)

        canvas_id = uuid.uuid4()
        chatroom_id = uuid.uuid4()

        batch_result = {"created": [], "updated": [], "deleted": 0}
        mock_batch.return_value = batch_result

        result = await svc.apply_template(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            template_id=template.id,
        )

        assert result == batch_result
        call_kwargs = mock_batch.await_args.kwargs
        assert call_kwargs["canvas_id"] == canvas_id
        assert call_kwargs["chatroom_id"] == chatroom_id
        creates = call_kwargs["creates"]
        assert len(creates) == 1
        assert creates[0]["kind"] == "note"
        assert creates[0]["content"] == "Idea 1"
        assert creates[0]["z_index"] == 1
        assert creates[0]["style"] == {"bg": "red"}


# ---- create_from_canvas -----------------------------------------------------


class TestCreateFromCanvas:
    @patch("contexts.canvas.application.template_service.audit")
    async def test_builds_template_from_canvas_objects(self, mock_audit: MagicMock) -> None:
        mock_audit.emit = AsyncMock()
        svc = _service()
        canvas_id = uuid.uuid4()
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()

        obj1 = _make_canvas_object(canvas_id=canvas_id, content="Note A")
        obj2 = _make_canvas_object(
            canvas_id=canvas_id,
            kind=CanvasObjectKind.TEXT,
            content="Text B",
        )

        svc._canvas_repo = MagicMock()
        svc._canvas_repo.list_objects = AsyncMock(return_value=[obj1, obj2])

        created_template = _make_template(
            scope=CanvasTemplateScope.PROJECT,
            project_id=project_id,
        )
        svc._template_repo = MagicMock()
        svc._template_repo.create = AsyncMock(return_value=created_template)

        result = await svc.create_from_canvas(
            canvas_id=canvas_id,
            project_id=project_id,
            name="From Canvas",
            description="Saved",
            actor_user_id=user_id,
        )

        assert result == created_template

        call_kwargs = svc._template_repo.create.await_args.kwargs
        values = call_kwargs["values"]
        assert values["scope"] == "project"
        assert values["project_id"] == project_id
        assert values["name"] == "From Canvas"

        objects = values["template_data"]["objects"]
        assert len(objects) == 2
        assert objects[0]["kind"] == "note"
        assert objects[0]["content"] == "Note A"
        assert objects[0]["position_x"] == 10.0
        assert objects[1]["kind"] == "text"
        assert objects[1]["content"] == "Text B"
