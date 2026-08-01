from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from contexts.knowledge.infrastructure.blob_store import MinioBlobStore


@pytest.mark.asyncio
async def test_download_to_path_streams_without_reading_whole_object(tmp_path: Path) -> None:
    response = MagicMock()
    response.stream.return_value = iter([b"first", b"-second"])
    client = MagicMock()
    client.get_object.return_value = response
    target = tmp_path / "source"

    await MinioBlobStore(client).download_to_path(bucket="sources", key="key", path=target)

    assert target.read_bytes() == b"first-second"
    response.stream.assert_called_once_with(amt=1024 * 1024)
    response.read.assert_not_called()
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
