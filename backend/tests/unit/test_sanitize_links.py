"""Sanitiser anchor hardening (R13.14) — reverse-tabnabbing protection."""

from __future__ import annotations

from shared_kernel.markdown.sanitize import render_safe_html, sanitize_html


def test_target_blank_gets_rel_noopener() -> None:
    out = sanitize_html('<a href="https://x.com" target="_blank">x</a>')
    assert 'target="_blank"' in out
    assert "noopener" in out
    assert "noreferrer" in out


def test_anchor_without_target_unchanged_rel() -> None:
    out = sanitize_html('<a href="https://x.com">y</a>')
    # No target -> no forced rel injected.
    assert "noopener" not in out


def test_supplied_rel_opener_is_overridden() -> None:
    out = sanitize_html('<a href="https://x.com" target="_blank" rel="opener">z</a>')
    assert "noopener noreferrer" in out
    # The unsafe opener token must not survive as the effective rel.
    assert 'rel="opener"' not in out


def test_markdown_link_still_renders() -> None:
    out = render_safe_html("[hi](https://x.com)")
    assert 'href="https://x.com"' in out


def test_script_still_stripped() -> None:
    out = sanitize_html('<a href="https://x.com" target="_blank">x</a><script>alert(1)</script>')
    assert "<script" not in out
