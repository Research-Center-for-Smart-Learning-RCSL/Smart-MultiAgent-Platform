"""Tests for the ClamAV INSTREAM scanner (shared_kernel.scanning)."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, patch

import pytest

from shared_kernel.scanning import (
    ClamAVScanner,
    ScanError,
    ScanResult,
    _parse_response,
)


class TestParseResponse:
    def test_clean(self):
        assert _parse_response(b"stream: OK\0") == ScanResult(clean=True)

    def test_clean_no_nul(self):
        assert _parse_response(b"stream: OK") == ScanResult(clean=True)

    def test_found(self):
        result = _parse_response(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
        assert not result.clean
        assert result.threat_name == "Win.Test.EICAR_HDB-1"

    def test_found_unknown(self):
        result = _parse_response(b"stream: FOUND\0")
        assert not result.clean
        assert result.threat_name == "unknown"

    def test_error(self):
        with pytest.raises(ScanError, match="clamd error"):
            _parse_response(b"stream: INSTREAM size limit exceeded. ERROR\0")

    def test_empty(self):
        with pytest.raises(ScanError, match="empty response"):
            _parse_response(b"\0")

    def test_unexpected(self):
        with pytest.raises(ScanError, match="unexpected clamd response"):
            _parse_response(b"gibberish\0")


class TestClamAVScanner:
    @pytest.mark.asyncio
    async def test_scan_clean(self):
        data = b"harmless content"
        scanner = ClamAVScanner(host="127.0.0.1", port=3310)

        mock_reader = AsyncMock()
        mock_reader.read = AsyncMock(return_value=b"stream: OK\0")

        mock_writer = AsyncMock()
        mock_writer.write = lambda x: None
        mock_writer.drain = AsyncMock()
        mock_writer.close = lambda: None
        mock_writer.wait_closed = AsyncMock()

        with patch("shared_kernel.scanning.asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            result = await scanner.scan(data)

        assert result.clean
        assert result.threat_name is None

    @pytest.mark.asyncio
    async def test_scan_threat_found(self):
        data = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR"
        scanner = ClamAVScanner(host="127.0.0.1", port=3310)

        mock_reader = AsyncMock()
        mock_reader.read = AsyncMock(return_value=b"stream: Win.Test.EICAR_HDB-1 FOUND\0")

        mock_writer = AsyncMock()
        mock_writer.write = lambda x: None
        mock_writer.drain = AsyncMock()
        mock_writer.close = lambda: None
        mock_writer.wait_closed = AsyncMock()

        with patch("shared_kernel.scanning.asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            result = await scanner.scan(data)

        assert not result.clean
        assert result.threat_name == "Win.Test.EICAR_HDB-1"

    @pytest.mark.asyncio
    async def test_connection_error(self):
        scanner = ClamAVScanner(host="192.0.2.1", port=3310, timeout=0.1)

        with (
            patch(
                "shared_kernel.scanning.asyncio.open_connection",
                side_effect=OSError("Connection refused"),
            ),
            pytest.raises(ScanError, match="cannot connect to clamd"),
        ):
            await scanner.scan(b"test")

    @pytest.mark.asyncio
    async def test_sends_instream_protocol(self):
        data = b"test payload"
        scanner = ClamAVScanner(host="127.0.0.1", port=3310)

        written_chunks: list[bytes] = []

        mock_reader = AsyncMock()
        mock_reader.read = AsyncMock(return_value=b"stream: OK\0")

        mock_writer = AsyncMock()
        mock_writer.write = lambda x: written_chunks.append(x)
        mock_writer.drain = AsyncMock()
        mock_writer.close = lambda: None
        mock_writer.wait_closed = AsyncMock()

        with patch("shared_kernel.scanning.asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            await scanner.scan(data)

        assert written_chunks[0] == b"zINSTREAM\0"
        assert written_chunks[1] == struct.pack(">I", len(data))
        assert written_chunks[2] == data
        assert written_chunks[3] == struct.pack(">I", 0)
