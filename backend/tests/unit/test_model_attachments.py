"""Model-facing attachment plumbing: neutral block assembly + per-adapter
translation so an agent can *see* an uploaded image / PDF / text file."""

from __future__ import annotations

import base64

from contexts.agents.application.runtime import model_attachments as ma
from contexts.keys.infrastructure.adapters.anthropic import _translate_messages as anthropic_tr
from contexts.keys.infrastructure.adapters.gemini import _contents as gemini_contents
from contexts.keys.infrastructure.adapters.openai import _messages as openai_messages


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"x" * 32


# --------------------------------------------------------------------------- #
# build_blocks
# --------------------------------------------------------------------------- #
def test_build_blocks_image_pdf_text() -> None:
    blocks = ma.build_blocks(
        [
            ("a.png", "image/png", _png()),
            ("b.pdf", "application/pdf", b"%PDF-1.4 body"),
            ("c.txt", "text/plain", b"hello world"),
        ]
    )
    kinds = [b["type"] for b in blocks]
    assert kinds == ["image", "document", "text"]
    assert blocks[0]["media_type"] == "image/png"
    assert base64.b64decode(blocks[0]["data"]) == _png()
    assert blocks[1]["media_type"] == "application/pdf"
    assert "hello world" in blocks[2]["text"]


def test_build_blocks_jpg_normalised_to_jpeg() -> None:
    blocks = ma.build_blocks([("p.jpg", "image/jpg", _png())])
    assert blocks[0]["media_type"] == "image/jpeg"


def test_build_blocks_unreadable_and_unsupported_become_notes() -> None:
    blocks = ma.build_blocks(
        [
            ("gone.png", "image/png", None),
            ("weird.bin", "application/octet-stream", b"\x00\x01"),
        ]
    )
    assert all(b["type"] == "text" for b in blocks)
    assert "unavailable" in blocks[0]["text"]
    assert "preview not supported" in blocks[1]["text"]


def test_build_blocks_oversized_file_becomes_note() -> None:
    big = b"x" * (ma.MAX_ATTACH_TOTAL_BYTES + 1)  # also over per-file cap
    blocks = ma.build_blocks([("big.png", "image/png", big)])
    assert blocks[0]["type"] == "text"
    assert "too large" in blocks[0]["text"]


def test_build_blocks_count_budget_notes_the_overflow() -> None:
    items = [(f"f{i}.png", "image/png", _png()) for i in range(ma.MAX_ATTACH_FILES + 2)]
    blocks = ma.build_blocks(items)
    images = [b for b in blocks if b["type"] == "image"]
    overflow = [b for b in blocks if b["type"] == "text"]
    assert len(images) == ma.MAX_ATTACH_FILES
    assert overflow
    assert "count budget" in overflow[0]["text"]


# --------------------------------------------------------------------------- #
# Anthropic — list content -> image/document blocks
# --------------------------------------------------------------------------- #
def test_anthropic_translates_image_and_document_blocks() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image", "media_type": "image/png", "data": "QUJD"},
                {"type": "document", "media_type": "application/pdf", "data": "REVG"},
            ],
        }
    ]
    out = anthropic_tr(msgs)
    blocks = out[0]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
    assert blocks[2]["type"] == "document"
    assert blocks[2]["source"]["media_type"] == "application/pdf"


# --------------------------------------------------------------------------- #
# OpenAI — image only for vision models; note otherwise
# --------------------------------------------------------------------------- #
def _list_msg() -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "media_type": "image/png", "data": "QUJD", "filename": "a.png"},
                {"type": "document", "media_type": "application/pdf", "data": "x", "filename": "b.pdf"},
            ],
        }
    ]


def test_openai_vision_model_emits_image_url_and_pdf_note() -> None:
    parts = openai_messages({"messages": _list_msg()}, "gpt-4o")[0]["content"]
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    # OpenAI Chat Completions PDF support is model-specific -> note, not a block.
    assert parts[2]["type"] == "text"
    assert "b.pdf" in parts[2]["text"]


def test_openai_non_vision_model_notes_the_image() -> None:
    parts = openai_messages({"messages": _list_msg()}, "gpt-3.5-turbo")[0]["content"]
    assert parts[1]["type"] == "text"
    assert "a.png" in parts[1]["text"]
    assert not any(p.get("type") == "image_url" for p in parts)


# --------------------------------------------------------------------------- #
# Gemini — inlineData for image + document
# --------------------------------------------------------------------------- #
def test_gemini_translates_blocks_to_inline_data() -> None:
    parts = gemini_contents({"messages": _list_msg()})[0]["parts"]
    assert parts[0] == {"text": "hi"}
    assert parts[1]["inlineData"] == {"mimeType": "image/png", "data": "QUJD"}
    assert parts[2]["inlineData"]["mimeType"] == "application/pdf"
