from __future__ import annotations

import base64
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.conversation.application import tus_service as tus_mod
from contexts.conversation.application.tus_service import TusService
from contexts.conversation.domain.errors import (
    TusStagingUnavailable,
    TusUploadCapacityExceeded,
)
from contexts.conversation.infrastructure.tus_store import (
    TusOffsetUpdateResult,
    TusReserveResult,
    TusUpload,
)


def _metadata() -> str:
    def encoded(value: str) -> str:
        return base64.b64encode(value.encode()).decode()

    return ",".join(
        (
            f"purpose {encoded('chat_attachment')}",
            f"filename {encoded('doc.txt')}",
            f"mime {encoded('text/plain')}",
            f"chatroom_id {encoded(str(uuid.uuid4()))}",
        ),
    )


def _service() -> TusService:
    with patch("contexts.conversation.application.tus_service.AttachmentService"):
        return TusService(db=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_rejects_before_reservation_when_staging_headroom_is_insufficient() -> None:
    service = _service()
    service._store = SimpleNamespace(create=AsyncMock())  # type: ignore[assignment]
    disk = SimpleNamespace(total=10_000, used=8_000, free=2_000)

    with (
        patch.object(tus_mod.shutil, "disk_usage", return_value=disk),
        patch.object(tus_mod, "TUS_STAGING_MIN_FREE_BYTES", 1_500),
        patch.object(tus_mod, "TUS_STAGING_MIN_FREE_RATIO", 0.10),
        pytest.raises(TusStagingUnavailable),
    ):
        await service.create(
            user_id=uuid.uuid4(),
            upload_length=501,
            metadata_raw=_metadata(),
            project_id=uuid.uuid4(),
        )

    service._store.create.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_releases_atomic_reservation_when_file_creation_fails(tmp_path: object) -> None:
    service = _service()
    store = SimpleNamespace(
        create=AsyncMock(return_value=TusReserveResult.ACCEPTED),
        delete=AsyncMock(),
    )
    service._store = store  # type: ignore[assignment]
    disk = SimpleNamespace(total=100_000, used=0, free=100_000)

    with (
        patch.object(tus_mod.shutil, "disk_usage", return_value=disk),
        patch.object(tus_mod, "TUS_STAGING_MIN_FREE_BYTES", 1),
        patch.object(tus_mod, "TUS_STAGING_MIN_FREE_RATIO", 0),
        patch.object(tus_mod, "_staging_path", return_value=os.path.join(str(tmp_path), "missing", "x.part")),
        pytest.raises(OSError),
    ):
        await service.create(
            user_id=uuid.uuid4(),
            upload_length=10,
            metadata_raw=_metadata(),
            project_id=uuid.uuid4(),
        )

    store.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_translates_reservation_limit_to_capacity_error() -> None:
    service = _service()
    service._store = SimpleNamespace(  # type: ignore[assignment]
        create=AsyncMock(return_value=TusReserveResult.USER_ACTIVE_LIMIT),
    )
    disk = SimpleNamespace(total=100_000, used=0, free=100_000)

    with (
        patch.object(tus_mod.shutil, "disk_usage", return_value=disk),
        patch.object(tus_mod, "TUS_STAGING_MIN_FREE_BYTES", 1),
        patch.object(tus_mod, "TUS_STAGING_MIN_FREE_RATIO", 0),
        pytest.raises(TusUploadCapacityExceeded),
    ):
        await service.create(
            user_id=uuid.uuid4(),
            upload_length=10,
            metadata_raw=_metadata(),
            project_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_patch_rejects_hourly_quota_before_writing(tmp_path: object) -> None:
    upload_id = uuid.uuid4()
    user_id = uuid.uuid4()
    path = os.path.join(str(tmp_path), "upload.part")
    with open(path, "wb"):
        pass
    upload = TusUpload(
        upload_id=upload_id,
        user_id=user_id,
        upload_length=10,
        upload_offset=0,
        purpose="chat_attachment",
        project_id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        rag_config_id=None,
        knowmap_config_id=None,
        filename="doc.txt",
        mime="text/plain",
        staging_path=path,
        metadata_raw="",
    )
    store = SimpleNamespace(
        get=AsyncMock(return_value=upload),
        update_offset=AsyncMock(return_value=TusOffsetUpdateResult.USER_HOURLY_LIMIT),
    )
    service = _service()
    service._store = store  # type: ignore[assignment]

    with pytest.raises(TusUploadCapacityExceeded):
        await service.patch(
            upload_id=upload_id,
            user_id=user_id,
            offset=0,
            chunk=b"x",
            actor_ip=None,
            request_id=None,
        )

    assert os.path.getsize(path) == 0
