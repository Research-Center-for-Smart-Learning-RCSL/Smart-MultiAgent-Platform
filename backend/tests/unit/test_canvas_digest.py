"""Unit tests for contexts.canvas.domain.canvas_digest.build_canvas_digest.

Pure function: takes a list of CanvasObject domain models and returns a
natural-language summary capped at 2000 characters, or None when empty.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from contexts.canvas.domain.canvas_digest import _MAX_DIGEST_CHARS, build_canvas_digest
from contexts.canvas.domain.models import CanvasObject, CanvasObjectKind

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _obj(
    kind: CanvasObjectKind = CanvasObjectKind.NOTE,
    content: str | None = None,
    minio_path: str | None = None,
) -> CanvasObject:
    return CanvasObject(
        id=uuid.uuid4(),
        canvas_id=uuid.uuid4(),
        kind=kind,
        position_x=0.0,
        position_y=0.0,
        width=100.0,
        height=100.0,
        z_index=0,
        content=content,
        minio_path=minio_path,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestEmptyCanvas:
    def test_returns_none_for_empty_list(self) -> None:
        assert build_canvas_digest([]) is None


class TestSummaryLine:
    def test_single_note(self) -> None:
        digest = build_canvas_digest([_obj(CanvasObjectKind.NOTE, content="hello")])
        assert digest is not None
        assert digest.startswith("The canvas contains 1 sticky note.")

    def test_multiple_objects_counted(self) -> None:
        objs = [
            _obj(CanvasObjectKind.NOTE, content="a"),
            _obj(CanvasObjectKind.NOTE, content="b"),
            _obj(CanvasObjectKind.TEXT, content="c"),
        ]
        digest = build_canvas_digest(objs)
        assert digest is not None
        assert "2 sticky notes" in digest
        assert "1 text block" in digest

    def test_all_kinds_represented(self) -> None:
        objs = [_obj(kind) for kind in CanvasObjectKind]
        digest = build_canvas_digest(objs)
        assert digest is not None
        for label in ("sticky note", "text block", "image", "shape", "freeform drawing", "connector"):
            assert label in digest


class TestObjectLines:
    def test_note_content_quoted(self) -> None:
        digest = build_canvas_digest([_obj(CanvasObjectKind.NOTE, content="My idea")])
        assert digest is not None
        assert '"My idea"' in digest

    def test_image_shows_filename(self) -> None:
        digest = build_canvas_digest(
            [_obj(CanvasObjectKind.IMAGE, minio_path="proj/canvas/abc/photo.png")]
        )
        assert digest is not None
        assert "photo.png" in digest

    def test_image_without_path_shows_fallback(self) -> None:
        digest = build_canvas_digest([_obj(CanvasObjectKind.IMAGE)])
        assert digest is not None
        assert "image" in digest.lower()

    def test_object_without_content_has_kind_only(self) -> None:
        digest = build_canvas_digest([_obj(CanvasObjectKind.SHAPE)])
        assert digest is not None
        assert "- Shape" in digest


class TestMarkdownEscaping:
    def test_special_chars_escaped(self) -> None:
        digest = build_canvas_digest([_obj(CanvasObjectKind.NOTE, content="**bold** [link](url)")])
        assert digest is not None
        assert "**" not in digest
        assert "\\*\\*bold\\*\\*" in digest


class TestTruncation:
    def test_digest_never_exceeds_max_chars(self) -> None:
        objs = [_obj(CanvasObjectKind.NOTE, content="x" * 200) for _ in range(50)]
        digest = build_canvas_digest(objs)
        assert digest is not None
        assert len(digest) <= _MAX_DIGEST_CHARS

    def test_truncation_marker_present_when_cut(self) -> None:
        objs = [_obj(CanvasObjectKind.NOTE, content="x" * 200) for _ in range(50)]
        digest = build_canvas_digest(objs)
        assert digest is not None
        assert "(truncated)" in digest or len(digest) == _MAX_DIGEST_CHARS

    def test_long_content_preview_truncated_at_200_chars(self) -> None:
        long_text = "a" * 500
        digest = build_canvas_digest([_obj(CanvasObjectKind.TEXT, content=long_text)])
        assert digest is not None
        assert ("a" * 201) not in digest
