"""Unit tests for prompt_studio.application.prompts.build_system_text (§29 / R29.15)."""

from __future__ import annotations

from contexts.prompt_studio.application.prompts import (
    DEFAULT_PERSONA,
    REFERENCE_MATERIAL_FRAMING,
    build_system_text,
)


def test_empty_persona_falls_back_to_default() -> None:
    text = build_system_text(
        persona_prompt="", config_system_prompt="", reference_texts=[], editor_draft=None
    )
    assert text.startswith(DEFAULT_PERSONA)


def test_custom_persona_replaces_default() -> None:
    text = build_system_text(
        persona_prompt="You are Marvin.",
        config_system_prompt="",
        reference_texts=[],
        editor_draft=None,
    )
    assert text.startswith("You are Marvin.")
    assert DEFAULT_PERSONA not in text


def test_reference_material_framing_always_present_with_custom_persona() -> None:
    # AC-11: prompt-injection defense must not depend on the persona author
    # remembering to include it.
    text = build_system_text(
        persona_prompt="You are Marvin.",
        config_system_prompt="",
        reference_texts=[],
        editor_draft=None,
    )
    assert REFERENCE_MATERIAL_FRAMING in text


def test_reference_material_framing_always_present_with_default_persona() -> None:
    text = build_system_text(
        persona_prompt="", config_system_prompt="", reference_texts=[], editor_draft=None
    )
    assert REFERENCE_MATERIAL_FRAMING in text


def test_whitespace_only_persona_falls_back_to_default() -> None:
    text = build_system_text(
        persona_prompt="   \n  ", config_system_prompt="", reference_texts=[], editor_draft=None
    )
    assert text.startswith(DEFAULT_PERSONA)


def test_full_assembly_order() -> None:
    text = build_system_text(
        persona_prompt="Persona.",
        config_system_prompt="Guidance.",
        reference_texts=[("a.txt", "ref content")],
        editor_draft="draft text",
    )
    assert text.index("Persona.") < text.index(REFERENCE_MATERIAL_FRAMING)
    assert text.index(REFERENCE_MATERIAL_FRAMING) < text.index("Guidance.")
    assert text.index("Guidance.") < text.index("ref content")
    assert text.index("ref content") < text.index("draft text")
