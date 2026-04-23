"""Prompt Read Strategy — R9.04–R9.08.

The loader is a *pure* component: it takes the raw `system_prompt` markdown
and the active provider's tool-use support flag, and produces one of

- ``FullPrompt(text=...)`` — entire prompt, or
- ``LazyPrompt(index=..., bodies={...})`` — an index prompt listing sections
  (title + description) plus a bank of section bodies that the model pulls
  via the `load_prompt_section(id)` tool.

SoC boundaries:

- **No DB access.** The caller reads the agent's `system_prompt` column.
- **No provider code.** The provider SDK layer supplies the `supports_tools`
  flag and registers the `load_prompt_section` tool; this module only
  defines the tool's contract (`LOAD_SECTION_TOOL_SPEC`) and the per-turn
  cache API.
- **No I/O.** Parsing runs synchronously against an in-memory string.

The turn-level cache (R9.07) is offered as a tiny `SectionCache` class that
the invocation pipeline instantiates fresh at every agent turn. Edits to
the `system_prompt` take effect on the *next* turn because the cache
expires with the turn (no stale bodies surviving a system_prompt rewrite).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "FullPrompt",
    "LOAD_SECTION_TOOL_SPEC",
    "LazyPrompt",
    "PromptAssembly",
    "PromptSection",
    "SectionCache",
    "assemble",
    "parse_sections",
]


# ---------------------------------------------------------------------------
# Section parsing — YAML-lite frontmatter on each section heading
# ---------------------------------------------------------------------------
#
# We do NOT pull in PyYAML just to parse three scalar fields. The expected
# section markup is:
#
#     ---
#     id: research
#     title: Research plan
#     description: How to plan multi-step research.
#     ---
#     body markdown…
#
# Separators repeat for every section. The parser is deliberately strict:
# anything before the first `---` is treated as a *preamble* that is always
# included verbatim in the index (it carries the agent's role statement).


_SEP_RE = re.compile(r"^---\s*$", re.MULTILINE)
_FM_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


@dataclass(frozen=True, slots=True)
class PromptSection:
    id: str
    title: str
    description: str
    body: str


def _parse_frontmatter(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        m = _FM_LINE_RE.match(line)
        if not m:
            raise ValueError(f"invalid front-matter line: {line!r}")
        out[m.group(1)] = m.group(2)
    return out


def parse_sections(text: str) -> tuple[str, list[PromptSection]]:
    """Split ``text`` into (preamble, [sections]).

    A section is any ``---…---`` block followed by its body. The preamble is
    everything before the first separator. Frontmatter requires *at least*
    ``id`` and ``title``; ``description`` defaults to an empty string.
    """
    parts = _SEP_RE.split(text)
    # `re.split` with the separator as a full-line match gives us the text
    # chunks on either side. Odd parts (1, 3, …) are frontmatter blocks;
    # even parts (2, 4, …) are section bodies. Part 0 is the preamble.
    preamble = parts[0].strip()
    sections: list[PromptSection] = []
    ids: set[str] = set()

    # We iterate in pairs: (frontmatter, body). Trailing unpaired frontmatter
    # (e.g. an author forgot the closing body) is a hard error.
    i = 1
    while i < len(parts):
        if i + 1 >= len(parts):
            raise ValueError("unterminated section front-matter at end of prompt")
        fm = _parse_frontmatter(parts[i])
        body = parts[i + 1].strip()
        sid = fm.get("id", "").strip()
        title = fm.get("title", "").strip()
        description = fm.get("description", "").strip()
        if not sid:
            raise ValueError("section front-matter missing `id`")
        if not title:
            raise ValueError(f"section {sid!r} missing `title`")
        if sid in ids:
            raise ValueError(f"duplicate section id {sid!r}")
        ids.add(sid)
        sections.append(
            PromptSection(id=sid, title=title, description=description, body=body)
        )
        i += 2
    return preamble, sections


# ---------------------------------------------------------------------------
# Tool spec — R9.06
# ---------------------------------------------------------------------------

LOAD_SECTION_TOOL_SPEC: dict[str, Any] = {
    "name": "load_prompt_section",
    "description": (
        "Fetch the full text of a prompt section by its id. "
        "Use this only when the section index shows the section is "
        "relevant to the current turn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The section id from the index.",
            }
        },
        "required": ["id"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Turn-level cache — R9.07
# ---------------------------------------------------------------------------


class SectionCache:
    """Per-turn cache. Instantiated once per agent turn; discarded after.

    Does not cross turns (R9.07 explicitly re-runs retrieval per turn), so
    there's no eviction logic — the object is just a thin dict wrapper that
    also records which ids were fetched so the pipeline can log token cost.
    """

    __slots__ = ("_store", "_fetched")

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._fetched: list[str] = []

    def get(self, section_id: str) -> str | None:
        return self._store.get(section_id)

    def put(self, section_id: str, body: str) -> None:
        if section_id not in self._store:
            self._store[section_id] = body
            self._fetched.append(section_id)

    @property
    def fetched_ids(self) -> tuple[str, ...]:
        return tuple(self._fetched)


# ---------------------------------------------------------------------------
# Assembled outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FullPrompt:
    mode: Literal["full"] = "full"
    text: str = ""


@dataclass(frozen=True, slots=True)
class LazyPrompt:
    mode: Literal["lazy"] = "lazy"
    index: str = ""
    bodies: dict[str, str] = field(default_factory=dict)
    tool_spec: dict[str, Any] = field(default_factory=lambda: dict(LOAD_SECTION_TOOL_SPEC))


PromptAssembly = FullPrompt | LazyPrompt


def _render_index(preamble: str, sections: list[PromptSection]) -> str:
    """Build the index prompt the model sees up-front.

    Format: preamble, then a "Available sections:" block listing
    ``- id — title: description`` lines. The model is told about the
    `load_prompt_section` tool out-of-band (tool registration in the LLM
    request payload).
    """
    lines: list[str] = []
    if preamble:
        lines.append(preamble)
        lines.append("")
    lines.append("Available prompt sections (call `load_prompt_section(id)` to fetch):")
    for s in sections:
        desc = f": {s.description}" if s.description else ""
        lines.append(f"- {s.id} — {s.title}{desc}")
    return "\n".join(lines).rstrip() + "\n"


def assemble(
    system_prompt: str,
    *,
    strategy: Literal["full", "lazy"],
    provider_supports_tools: bool,
) -> PromptAssembly:
    """Render the agent's system prompt according to the strategy.

    R9.08 fallback: if ``strategy == "lazy"`` but the provider cannot use
    tools, silently degrade to ``full`` (the caller is responsible for
    emitting the UI warning — this module has no UI surface).
    """
    if strategy == "full" or not provider_supports_tools:
        return FullPrompt(text=system_prompt)

    preamble, sections = parse_sections(system_prompt)
    if not sections:
        # A `lazy` prompt with no sections is equivalent to `full` — the
        # index would just be the preamble.
        return FullPrompt(text=preamble or system_prompt)

    index = _render_index(preamble, sections)
    bodies = {s.id: s.body for s in sections}
    return LazyPrompt(index=index, bodies=bodies)
