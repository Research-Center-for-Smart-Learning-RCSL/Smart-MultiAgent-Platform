"""CRDT relay -- manages one in-memory Yjs document per active canvas.

Uses ``pycrdt`` to validate, apply, and persist Yjs updates. Each canvas
gets an asyncio lock to serialize updates and prevent concurrent corruption.

The relay is a singleton registry keyed by ``canvas_id``. Documents are
loaded from ``canvases.crdt_state`` on first access and evicted on last
editor disconnect.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pycrdt

from contexts.canvas.domain.models import CanvasObject

_log = logging.getLogger(__name__)

_MAX_DOC_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB cap per [R13.51]

_EXCALIDRAW_ELEMENT_KINDS = {
    "note": "rectangle",
    "text": "text",
    "image": "image",
    "shape": "rectangle",
    "drawing": "freedraw",
    "connector": "arrow",
}


def build_element_dict(
    kind: str,
    *,
    elem_id: str | None = None,
    x: float = 0,
    y: float = 0,
    width: float = 100,
    height: float = 100,
    content: str | None = None,
    style: dict[str, Any] | None = None,
    custom_data: dict[str, Any] | None = None,
    image: bool = False,
) -> dict[str, Any]:
    """Build an Excalidraw element dict with kind-specific defaults.

    Image elements get transparent stroke and roughness 0; all others get
    the standard Excalidraw defaults.
    """
    eid = elem_id or str(uuid.uuid4())
    elem: dict[str, Any] = {
        "id": eid,
        "type": _EXCALIDRAW_ELEMENT_KINDS.get(kind, "rectangle") if not image else "image",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "angle": 0,
        "strokeColor": "transparent" if image else "#1e1e1e",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 0 if image else 2,
        "roughness": 0 if image else 1,
        "opacity": 100,
        "isDeleted": False,
        "groupIds": [],
        "boundElements": None,
        "updated": 1,
        "locked": False,
    }
    if image:
        elem["fileId"] = eid
        elem["status"] = "saved"
    if content:
        elem["text"] = content
        if kind == "text":
            elem["fontSize"] = 20
            elem["fontFamily"] = 1
            elem["textAlign"] = "left"
            elem["verticalAlign"] = "top"
    if style:
        if "backgroundColor" in style:
            elem["backgroundColor"] = style["backgroundColor"]
        if "strokeColor" in style:
            elem["strokeColor"] = style["strokeColor"]
    if custom_data:
        elem["customData"] = custom_data
    return elem


class CrdtUpdateError(Exception):
    """Raised when an update is rejected (malformed or oversized)."""


@dataclass
class _CanvasDoc:
    doc: pycrdt.Doc  # type: ignore[type-arg]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dirty: bool = False


class CrdtRelay:
    """Singleton CRDT document registry."""

    def __init__(self) -> None:
        self._docs: dict[uuid.UUID, _CanvasDoc] = {}

    def has(self, canvas_id: uuid.UUID) -> bool:
        return canvas_id in self._docs

    async def get_or_load(
        self,
        canvas_id: uuid.UUID,
        *,
        crdt_state: bytes | None = None,
        legacy_objects: Sequence[CanvasObject] | None = None,
    ) -> pycrdt.Doc:  # type: ignore[type-arg]
        """Return the in-memory doc, loading or migrating as needed.

        ``crdt_state`` is the persisted BYTEA from the database.
        ``legacy_objects`` are Phase 1 canvas_objects for migration ([R13.54]).
        """
        if canvas_id in self._docs:
            return self._docs[canvas_id].doc

        doc: pycrdt.Doc = pycrdt.Doc()  # type: ignore[type-arg]
        if crdt_state:
            doc.apply_update(crdt_state)
        elif legacy_objects:
            self._migrate_from_objects(doc, legacy_objects)

        self._docs[canvas_id] = _CanvasDoc(doc=doc)
        return doc

    def _migrate_from_objects(
        self,
        doc: pycrdt.Doc,  # type: ignore[type-arg]
        objects: Sequence[CanvasObject],
    ) -> None:
        """Build a Yjs doc from Phase 1 canvas_objects ([R13.54]).

        Creates a Y.Map keyed by element ID (``excalidraw-elements``).
        """
        elements_map = doc.get("excalidraw-elements", type=pycrdt.Map)
        for obj in objects:
            elem_id = str(obj.id)
            element = build_element_dict(
                obj.kind.value,
                elem_id=elem_id,
                x=obj.position_x,
                y=obj.position_y,
                width=obj.width,
                height=obj.height,
                content=obj.content,
                style=obj.style,
            )
            elements_map[elem_id] = pycrdt.Map(element)

    async def apply_update(
        self,
        canvas_id: uuid.UUID,
        update_b64: str,
    ) -> str:
        """Validate and apply a base64-encoded Yjs update. Returns encoded delta.

        Raises ``CrdtUpdateError`` on malformed or oversized updates.
        """
        entry = self._docs.get(canvas_id)
        if entry is None:
            raise CrdtUpdateError("canvas not loaded")

        try:
            update_bytes = base64.b64decode(update_b64)
        except Exception as exc:
            raise CrdtUpdateError("invalid base64") from exc

        async with entry.lock:
            # Validate by applying to a scratch doc first (size cap check)
            scratch: pycrdt.Doc = pycrdt.Doc()  # type: ignore[type-arg]
            scratch.apply_update(entry.doc.get_update())
            try:
                scratch.apply_update(update_bytes)
            except Exception as exc:
                raise CrdtUpdateError(f"malformed update: {exc}") from exc

            encoded = scratch.get_update()
            if len(encoded) > _MAX_DOC_SIZE_BYTES:
                raise CrdtUpdateError(f"document would exceed {_MAX_DOC_SIZE_BYTES} byte cap")

            # Apply to the real doc
            try:
                entry.doc.apply_update(update_bytes)
            except Exception as exc:
                raise CrdtUpdateError(f"apply failed: {exc}") from exc
            entry.dirty = True

        return update_b64

    def get_state_as_b64(self, canvas_id: uuid.UUID) -> str | None:
        """Return the full Yjs doc state as base64, or None if not loaded."""
        entry = self._docs.get(canvas_id)
        if entry is None:
            return None
        return base64.b64encode(entry.doc.get_update()).decode("ascii")

    def get_state_vector_b64(self, canvas_id: uuid.UUID) -> str | None:
        """Return the Yjs state vector as base64, or None if not loaded."""
        entry = self._docs.get(canvas_id)
        if entry is None:
            return None
        return base64.b64encode(entry.doc.get_state()).decode("ascii")

    def get_state_bytes(self, canvas_id: uuid.UUID) -> bytes | None:
        """Return the raw Yjs doc state for persistence."""
        entry = self._docs.get(canvas_id)
        if entry is None:
            return None
        return entry.doc.get_update()

    def should_flush(self, canvas_id: uuid.UUID) -> bool:
        """True if the doc has changed since last flush."""
        entry = self._docs.get(canvas_id)
        return entry is not None and entry.dirty

    def mark_flushed(self, canvas_id: uuid.UUID) -> None:
        """Mark the doc as persisted."""
        entry = self._docs.get(canvas_id)
        if entry is not None:
            entry.dirty = False

    def evict(self, canvas_id: uuid.UUID) -> None:
        """Remove from in-memory cache (on last disconnect)."""
        self._docs.pop(canvas_id, None)

    async def inject_elements(
        self,
        canvas_id: uuid.UUID,
        elements: list[dict[str, Any]],
        *,
        crdt_state: bytes | None = None,
        replace: bool = False,
    ) -> tuple[bytes, bytes]:
        """Write Excalidraw elements into the CRDT doc from the server side.

        Loads the doc if not in memory.  If *replace* is True, marks every
        existing element as deleted first (used by snapshot restore).

        Returns ``(full_state, delta)`` -- full state for persistence,
        incremental delta for broadcasting.
        """
        doc = await self.get_or_load(canvas_id, crdt_state=crdt_state)
        entry = self._docs[canvas_id]

        async with entry.lock:
            pre_sv = doc.get_state()
            elements_map = doc.get("excalidraw-elements", type=pycrdt.Map)

            if replace:
                for key in list(elements_map):
                    item = elements_map[key]
                    if isinstance(item, pycrdt.Map):
                        item["isDeleted"] = True

            for elem in elements:
                elem_id = elem.get("id") or str(uuid.uuid4())
                elements_map[elem_id] = pycrdt.Map(elem)

            full_state = doc.get_update()
            if len(full_state) > _MAX_DOC_SIZE_BYTES:
                raise CrdtUpdateError(f"document would exceed {_MAX_DOC_SIZE_BYTES} byte cap")

            delta = doc.get_update(pre_sv)
            entry.dirty = True

        return full_state, delta

    async def update_element(
        self,
        canvas_id: uuid.UUID,
        element_id: str,
        fields: dict[str, Any],
        *,
        crdt_state: bytes | None = None,
    ) -> tuple[bytes, bytes]:
        """Merge *fields* into an existing Excalidraw element in the CRDT doc.

        Returns ``(full_state, delta)``.
        """
        doc = await self.get_or_load(canvas_id, crdt_state=crdt_state)
        entry = self._docs[canvas_id]

        async with entry.lock:
            pre_sv = doc.get_state()
            elements_map = doc.get("excalidraw-elements", type=pycrdt.Map)
            existing = elements_map.get(element_id)
            if not isinstance(existing, pycrdt.Map):
                raise CrdtUpdateError(f"element {element_id} not found")

            for key, value in fields.items():
                existing[key] = value

            full_state = doc.get_update()
            if len(full_state) > _MAX_DOC_SIZE_BYTES:
                raise CrdtUpdateError(f"document would exceed {_MAX_DOC_SIZE_BYTES} byte cap")

            delta = doc.get_update(pre_sv)
            entry.dirty = True

        return full_state, delta

    async def delete_element(
        self,
        canvas_id: uuid.UUID,
        element_id: str,
        *,
        crdt_state: bytes | None = None,
    ) -> tuple[bytes, bytes]:
        """Soft-delete an element by setting ``isDeleted: true``.

        Returns ``(full_state, delta)``.
        """
        doc = await self.get_or_load(canvas_id, crdt_state=crdt_state)
        entry = self._docs[canvas_id]

        async with entry.lock:
            pre_sv = doc.get_state()
            elements_map = doc.get("excalidraw-elements", type=pycrdt.Map)
            existing = elements_map.get(element_id)
            if not isinstance(existing, pycrdt.Map):
                raise CrdtUpdateError(f"element {element_id} not found")

            existing["isDeleted"] = True

            full_state = doc.get_update()
            delta = doc.get_update(pre_sv)
            entry.dirty = True

        return full_state, delta

    def extract_elements_for_digest(
        self,
        canvas_id: uuid.UUID,
    ) -> list[dict[str, Any]] | None:
        """Extract element data from the Yjs doc for agent digest."""
        entry = self._docs.get(canvas_id)
        if entry is None:
            return None
        try:
            return _extract_elements_from_doc(entry.doc)
        except Exception:
            _log.warning("failed to extract elements from CRDT doc %s", canvas_id, exc_info=True)
            return None


def _extract_elements_from_doc(doc: pycrdt.Doc) -> list[dict[str, Any]]:  # type: ignore[type-arg]
    """Shared extraction: reads from ``excalidraw-elements`` Y.Map."""
    elements_map = doc.get("excalidraw-elements", type=pycrdt.Map)
    result: list[dict[str, Any]] = []
    for key in elements_map:
        item = elements_map[key]
        if isinstance(item, pycrdt.Map):
            elem = dict(item)
        elif isinstance(item, dict):
            elem = item
        else:
            continue
        if not elem.get("isDeleted"):
            result.append(elem)
    return result


# Singleton instance
_relay = CrdtRelay()


def get_crdt_relay() -> CrdtRelay:
    return _relay


__all__ = [
    "CrdtRelay",
    "CrdtUpdateError",
    "build_element_dict",
    "get_crdt_relay",
]
