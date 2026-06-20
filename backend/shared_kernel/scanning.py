"""File scanning abstraction + ClamAV adapter (R22.15.07).

The ClamAV INSTREAM protocol is straightforward:
  1. Open TCP to clamd (default port 3310).
  2. Send ``zINSTREAM\\0``.
  3. Send data in length-prefixed chunks (4-byte big-endian size + payload).
  4. Send a zero-length chunk (4 zero bytes) to signal EOF.
  5. Read the one-line response: ``stream: OK\\0`` or
     ``stream: <threat> FOUND\\0``.

No third-party library needed — native ``asyncio.open_connection`` suffices.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ScanError(RuntimeError):
    """Raised when the AV engine is unreachable or returns a malformed response."""


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    threat_name: str | None = None


@runtime_checkable
class FileScanner(Protocol):
    async def scan(self, data: bytes) -> ScanResult: ...


_CHUNK_SIZE = 8192


class ClamAVScanner:
    """Async ClamAV scanner using the clamd INSTREAM protocol."""

    def __init__(self, host: str, port: int = 3310, timeout: float = 120.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    async def scan(self, data: bytes) -> ScanResult:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as exc:
            raise ScanError(f"cannot connect to clamd at {self._host}:{self._port}: {exc}") from exc

        try:
            writer.write(b"zINSTREAM\0")

            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + _CHUNK_SIZE]
                writer.write(struct.pack(">I", len(chunk)))
                writer.write(chunk)
                offset += len(chunk)

            writer.write(struct.pack(">I", 0))
            await writer.drain()

            try:
                response = await asyncio.wait_for(reader.read(4096), timeout=self._timeout)
            except TimeoutError as exc:
                raise ScanError(f"clamd read timeout after {self._timeout}s") from exc
            return _parse_response(response)
        finally:
            writer.close()
            with _suppress_closed():
                await writer.wait_closed()


def _parse_response(raw: bytes) -> ScanResult:
    text = raw.rstrip(b"\0").decode("utf-8", errors="replace").strip()
    if not text:
        raise ScanError("empty response from clamd")
    if text.endswith("OK"):
        return ScanResult(clean=True)
    if text.endswith("FOUND"):
        # Format: "stream: Win.Test.EICAR_HDB-1 FOUND"
        threat = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return ScanResult(clean=False, threat_name=threat or "unknown")
    if "ERROR" in text:
        raise ScanError(f"clamd error: {text}")
    raise ScanError(f"unexpected clamd response: {text}")


def _suppress_closed():
    import contextlib

    return contextlib.suppress(OSError, ConnectionError)


def get_scanner() -> FileScanner | None:
    from app.config.settings import get_settings

    sec = get_settings().security
    if not sec.file_scan_enabled or sec.clamav_host is None:
        return None
    return ClamAVScanner(host=sec.clamav_host, port=sec.clamav_port)


__all__ = [
    "ClamAVScanner",
    "FileScanner",
    "ScanError",
    "ScanResult",
    "get_scanner",
]
