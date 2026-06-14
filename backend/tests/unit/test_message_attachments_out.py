"""M.5.5 — MessageOut exposes a message's attachments (R13.11).

The read API previously returned no attachments, so the client had no ids to
download (or to render `[attachment expired]`). These cover the pure shaping:
`_to_out` embeds the attachments and `_att_out` maps enum status → str.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.api.v1.messages import _att_out, _to_out


def _msg() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        sender_type=SimpleNamespace(value="user"),
        sender_id=uuid.uuid4(),
        content_md="hi",
        metadata={},
        version=1,
        created_at=None,
        edited_at=None,
        deleted_at=None,
    )


def _att(*, status: str = "active", scan: str = "clean") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        filename="guide.pdf",
        mime="application/pdf",
        size_bytes=2048,
        status=SimpleNamespace(value=status),
        scan_status=SimpleNamespace(value=scan),
    )


def test_to_out_has_no_attachments_by_default() -> None:
    assert _to_out(_msg()).attachments == []


def test_to_out_embeds_attachments() -> None:
    out = _to_out(_msg(), [_att(), _att(status="expired")])
    assert len(out.attachments) == 2
    assert {a.filename for a in out.attachments} == {"guide.pdf"}
    # Expired rows are returned (not filtered) so the UI can show [attachment expired].
    assert "expired" in {a.status for a in out.attachments}


def test_att_out_maps_enum_status_to_str() -> None:
    out = _att_out(_att(status="quarantined", scan="quarantined"))
    assert out.status == "quarantined"
    assert out.scan_status == "quarantined"
