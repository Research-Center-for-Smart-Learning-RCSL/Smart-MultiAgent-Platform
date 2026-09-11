"""Unit tests for contexts.canvas.application.crdt_relay.

Tests the CrdtRelay singleton's document lifecycle: creation, update
application, size cap enforcement, malformed update rejection, state vector
generation, flush tracking, migration from Phase 1 objects, and eviction.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pycrdt
import pytest

from contexts.canvas.application.crdt_relay import CrdtRelay, CrdtUpdateError
from contexts.canvas.domain.models import CanvasObject, CanvasObjectKind

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_object(
    kind: str = "note",
    content: str | None = "hello",
    x: float = 10.0,
    y: float = 20.0,
) -> CanvasObject:
    return CanvasObject(
        id=uuid.uuid4(),
        canvas_id=uuid.uuid4(),
        kind=CanvasObjectKind(kind),
        position_x=x,
        position_y=y,
        width=100.0,
        height=80.0,
        z_index=0,
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_update_b64() -> str:
    """Create a valid Yjs update as base64."""
    doc = pycrdt.Doc()
    elements = doc.get("elements", type=pycrdt.Array)
    elements.append(pycrdt.Map({"id": str(uuid.uuid4()), "type": "rectangle", "x": 0, "y": 0}))
    return base64.b64encode(doc.get_update()).decode("ascii")


class TestGetOrLoad:
    async def test_creates_empty_doc(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        doc = await relay.get_or_load(canvas_id)
        assert isinstance(doc, pycrdt.Doc)
        assert relay.has(canvas_id)

    async def test_loads_from_crdt_state(self) -> None:
        # Prepare persisted state
        src = pycrdt.Doc()
        elems = src.get("elements", type=pycrdt.Array)
        elems.append(pycrdt.Map({"id": "e1", "type": "text"}))
        state = src.get_update()

        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        doc = await relay.get_or_load(canvas_id, crdt_state=state)

        loaded_elems = doc.get("elements", type=pycrdt.Array)
        assert len(loaded_elems) == 1

    async def test_migrates_from_legacy_objects(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        objects = [_make_object("note", "sticky"), _make_object("text", "hello")]
        doc = await relay.get_or_load(canvas_id, legacy_objects=objects)

        elements_map = doc.get("excalidraw-elements", type=pycrdt.Map)
        assert len(elements_map) == 2

    async def test_returns_cached_on_second_call(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        doc1 = await relay.get_or_load(canvas_id)
        doc2 = await relay.get_or_load(canvas_id)
        assert doc1 is doc2


class TestApplyUpdate:
    async def test_valid_update(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)

        update_b64 = _make_update_b64()
        result = await relay.apply_update(canvas_id, update_b64)
        assert result == update_b64
        assert relay.should_flush(canvas_id)

    async def test_invalid_base64_rejected(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)

        with pytest.raises(CrdtUpdateError, match="invalid base64"):
            await relay.apply_update(canvas_id, "not-valid-base64!!!")

    async def test_malformed_update_rejected(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)

        garbage = base64.b64encode(b"\x00\x01\x02\x03\xff").decode("ascii")
        with pytest.raises(CrdtUpdateError, match="malformed"):
            await relay.apply_update(canvas_id, garbage)

    async def test_canvas_not_loaded_rejected(self) -> None:
        relay = CrdtRelay()
        with pytest.raises(CrdtUpdateError, match="canvas not loaded"):
            await relay.apply_update(uuid.uuid4(), _make_update_b64())

    async def test_size_cap_enforcement(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)

        # Build a doc that clearly exceeds the 10 MB cap
        doc = pycrdt.Doc()
        elems = doc.get("elements", type=pycrdt.Array)
        for i in range(600):
            elems.append(pycrdt.Map({"id": str(i), "data": "x" * 20000}))
        big_update = base64.b64encode(doc.get_update()).decode("ascii")

        with pytest.raises(CrdtUpdateError, match="cap"):
            await relay.apply_update(canvas_id, big_update)


class TestStateAccess:
    async def test_get_state_as_b64(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)
        result = relay.get_state_as_b64(canvas_id)
        assert result is not None
        # Should be valid base64
        base64.b64decode(result)

    async def test_get_state_vector_b64(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)
        result = relay.get_state_vector_b64(canvas_id)
        assert result is not None

    async def test_get_state_bytes(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)
        result = relay.get_state_bytes(canvas_id)
        assert isinstance(result, bytes)

    async def test_not_loaded_returns_none(self) -> None:
        relay = CrdtRelay()
        assert relay.get_state_as_b64(uuid.uuid4()) is None
        assert relay.get_state_vector_b64(uuid.uuid4()) is None
        assert relay.get_state_bytes(uuid.uuid4()) is None


class TestFlush:
    async def test_dirty_tracking(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)

        assert not relay.should_flush(canvas_id)

        await relay.apply_update(canvas_id, _make_update_b64())
        assert relay.should_flush(canvas_id)

        relay.mark_flushed(canvas_id)
        assert not relay.should_flush(canvas_id)


class TestEviction:
    async def test_evict(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        await relay.get_or_load(canvas_id)
        assert relay.has(canvas_id)

        relay.evict(canvas_id)
        assert not relay.has(canvas_id)

    async def test_evict_nonexistent_is_noop(self) -> None:
        relay = CrdtRelay()
        relay.evict(uuid.uuid4())  # no error


class TestExtractElements:
    async def test_extracts_elements(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        objects = [_make_object("note", "test note"), _make_object("text", "test text")]
        await relay.get_or_load(canvas_id, legacy_objects=objects)

        elements = relay.extract_elements_for_digest(canvas_id)
        assert elements is not None
        assert len(elements) == 2

    async def test_not_loaded_returns_none(self) -> None:
        relay = CrdtRelay()
        assert relay.extract_elements_for_digest(uuid.uuid4()) is None

    async def test_skips_deleted_elements(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()

        doc = pycrdt.Doc()
        elems_map = doc.get("excalidraw-elements", type=pycrdt.Map)
        elems_map["e1"] = pycrdt.Map({"id": "e1", "type": "rect", "isDeleted": False})
        elems_map["e2"] = pycrdt.Map({"id": "e2", "type": "rect", "isDeleted": True})
        state = doc.get_update()

        await relay.get_or_load(canvas_id, crdt_state=state)
        elements = relay.extract_elements_for_digest(canvas_id)
        assert elements is not None
        assert len(elements) == 1
        assert elements[0]["id"] == "e1"
