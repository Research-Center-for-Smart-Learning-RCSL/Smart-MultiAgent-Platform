"""Unit tests for `contexts.agents.application.prompt_loader` (E.2)."""

from __future__ import annotations

import pytest

from contexts.agents.application.prompt_loader import (
    FullPrompt,
    LazyPrompt,
    assemble,
    parse_sections,
)


def test_full_always_returns_fullprompt() -> None:
    out = assemble("hello", strategy="full", provider_supports_tools=True)
    assert isinstance(out, FullPrompt)
    assert out.text == "hello"


def test_lazy_falls_back_when_provider_lacks_tools() -> None:
    # R9.08 — silent degrade to full when tool use unsupported.
    md = "pre\n---\nid: a\ntitle: A\n---\nbody"
    out = assemble(md, strategy="lazy", provider_supports_tools=False)
    assert isinstance(out, FullPrompt)


def test_lazy_builds_index_and_bodies() -> None:
    md = (
        "Preamble.\n\n"
        "---\nid: s1\ntitle: First\ndescription: One\n---\nbody1\n\n"
        "---\nid: s2\ntitle: Second\n---\nbody2"
    )
    out = assemble(md, strategy="lazy", provider_supports_tools=True)
    assert isinstance(out, LazyPrompt)
    assert "Preamble." in out.index
    assert "s1 — First: One" in out.index
    assert "s2 — Second" in out.index
    assert out.bodies["s1"] == "body1"
    assert out.bodies["s2"] == "body2"


def test_lazy_with_no_sections_is_effectively_full() -> None:
    out = assemble("just preamble", strategy="lazy", provider_supports_tools=True)
    assert isinstance(out, FullPrompt)
    assert out.text == "just preamble"


def test_duplicate_section_id_rejected() -> None:
    md = "---\nid: x\ntitle: A\n---\nb\n---\nid: x\ntitle: B\n---\nc"
    with pytest.raises(ValueError, match="duplicate section id"):
        parse_sections(md)


def test_missing_title_rejected() -> None:
    md = "---\nid: x\n---\nbody"
    with pytest.raises(ValueError, match="missing `title`"):
        parse_sections(md)
