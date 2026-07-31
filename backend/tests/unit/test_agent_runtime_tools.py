"""K.2 — per-turn tool registry: update_wakeup, read_skill (§31), dispatch."""

from __future__ import annotations

import json
import uuid
from typing import ClassVar

import pytest
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from contexts.agents.application.runtime import tool_registry as tr
from contexts.agents.application.runtime.tool_registry import (
    _MAX_TOOL_OUTPUT,
    _SKILL_BODY_TOKEN_BUDGET,
    BUILTIN_TOOL_NAMES,
    ToolRegistry,
    build_read_skill_tool,
    build_registry,
    build_update_wakeup_tool,
)
from contexts.skills.application.binding_service import BoundSet
from contexts.skills.domain.models import Skill, SkillFile, SkillFileKind, SkillRead, SkillScanStatus
from shared_kernel.tokens import estimate_tokens
from tests.unit.skill_fakes import NOW, make_skill


def _snap(*skills: Skill, files: dict | None = None) -> BoundSet:
    """The turn snapshot the tool reads.

    `build_registry` takes the whole `BoundSet` rather than a list of skills, so the
    bodies, the file manifest, and the scan statuses that gate them cannot drift apart
    between the tap and the tool.
    """
    return BoundSet(skills=tuple(skills), files=files or {})


@pytest.mark.asyncio
async def test_update_wakeup_tool_invokes_facade(monkeypatch) -> None:
    captured: dict = {}

    class _Cfg:
        def to_dict(self) -> dict:
            return {"triggers": {"every_n_messages": {"n": 3}}}

    class _Facade:
        def __init__(self, _db) -> None:
            pass

        async def update_wakeup(self, *, agent_id, every_n_messages, silence_minutes, actor_agent_id):
            captured.update(
                agent_id=agent_id,
                every_n_messages=every_n_messages,
                silence_minutes=silence_minutes,
                actor_agent_id=actor_agent_id,
            )
            return _Cfg()

    import contexts.orchestration.interfaces.facade as facade_mod

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)
    agent_id = uuid.uuid4()
    tool = build_update_wakeup_tool(object(), agent_id=agent_id)

    res = await tool.invoke({"every_n_messages": 3})

    assert not res.is_error
    assert json.loads(res.content) == {"triggers": {"every_n_messages": {"n": 3}}}
    assert captured["every_n_messages"] == 3
    assert captured["silence_minutes"] is None
    assert captured["agent_id"] == agent_id
    assert captured["actor_agent_id"] == agent_id


@pytest.mark.asyncio
async def test_registry_dispatch_and_unknown() -> None:
    reg = build_registry(object(), agent_id=uuid.uuid4(), skills=_snap())
    # update_wakeup is the only unconditional built-in: an agent with nothing bound
    # is not offered read_skill and pays no tokens for a tool it cannot use.
    assert len(reg) == 1
    assert reg.get("update_wakeup") is not None
    assert {s["name"] for s in reg.specs()} == {"update_wakeup"}

    unknown = await ToolRegistry([]).call("nope", {})
    assert unknown.is_error


@pytest.mark.asyncio
async def test_registry_swallows_tool_exception() -> None:
    """A domain failure stays a tool result: the model can act on it."""
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _boom(_args):
        raise RuntimeError("kaboom")

    reg = ToolRegistry(
        [Tool(name="x", description="d", input_schema={}, invoke=_boom)],
        is_infra_error=_is_db_error,
    )
    res = await reg.call("x", {})
    assert res.is_error
    assert "kaboom" in res.content


def _is_db_error(exc: BaseException) -> bool:
    return isinstance(exc, SQLAlchemyError)


@pytest.mark.asyncio
async def test_registry_reraises_infrastructure_errors() -> None:
    """An infrastructure fault is not a tool outcome (AC-3).

    Reported to the model, it would be a fact the model cannot act on, and the
    turn would keep buying provider tokens against a transaction whose reply can
    no longer be written.
    """
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _db_down(_args):
        raise OperationalError("SELECT secret FROM keys WHERE id = 'k-123'", {}, Exception("down"))

    reg = ToolRegistry(
        [Tool(name="x", description="d", input_schema={}, invoke=_db_down)],
        is_infra_error=_is_db_error,
    )

    with pytest.raises(OperationalError):
        await reg.call("x", {})


@pytest.mark.asyncio
async def test_registry_without_a_classifier_degrades_everything() -> None:
    """The seam is opt-in: a registry built without one behaves as before."""
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _db_down(_args):
        raise OperationalError("stmt", {}, Exception("down"))

    reg = ToolRegistry([Tool(name="x", description="d", input_schema={}, invoke=_db_down)])
    assert (await reg.call("x", {})).is_error


@pytest.mark.asyncio
async def test_registry_rejects_arguments_violating_input_schema() -> None:
    """AC-6. Nothing stood between the model's arguments and the tool: every tool
    coerced instead, so a missing required field became a default and the call
    came back a success."""
    from contexts.agents.application.runtime.builtin_tools import _CODE_EXEC_SCHEMA
    from contexts.agents.application.runtime.tool_registry import Tool

    invoked: list = []

    async def _record(args):
        invoked.append(args)
        return tr.ToolResult(content="ran")

    reg = ToolRegistry(
        [Tool(name="code_exec", description="d", input_schema=_CODE_EXEC_SCHEMA, invoke=_record)]
    )

    res = await reg.call("code_exec", {})

    assert res.is_error
    assert "source" in res.content
    assert invoked == []


@pytest.mark.asyncio
async def test_registry_dispatches_arguments_that_satisfy_the_schema() -> None:
    from contexts.agents.application.runtime.builtin_tools import _CODE_EXEC_SCHEMA
    from contexts.agents.application.runtime.tool_registry import Tool

    invoked: list = []

    async def _record(args):
        invoked.append(args)
        return tr.ToolResult(content="ran")

    reg = ToolRegistry(
        [Tool(name="code_exec", description="d", input_schema=_CODE_EXEC_SCHEMA, invoke=_record)]
    )

    res = await reg.call("code_exec", {"source": "print(1)"})

    assert not res.is_error
    assert invoked == [{"source": "print(1)"}]


@pytest.mark.asyncio
async def test_an_unusable_input_schema_costs_validation_not_the_turn() -> None:
    """An MCP server's captured contract is written by someone else."""
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _ran(_args):
        return tr.ToolResult(content="ran")

    reg = ToolRegistry([Tool(name="x", description="d", input_schema={"type": "not-a-type"}, invoke=_ran)])

    assert (await reg.call("x", {})).content == "ran"


def test_a_hostile_regex_in_an_untrusted_schema_cannot_stall_the_worker() -> None:
    """A tool's `input_schema` is not ours: a LOCAL_FUNCTION carries whatever
    `parameters` its author wrote and an MCP binding whatever its server returned.
    Running an attacker-chosen regex against model-written arguments is
    catastrophic backtracking on demand — and it blocks the event loop, so it
    stalls every concurrent turn on the worker, not just this one.

    Measured before the fix: 12s at 28 characters, 45s at 30.
    """
    import time

    schema = {"type": "object", "properties": {"q": {"type": "string", "pattern": "^(a+)+$"}}}

    start = time.monotonic()
    violations = tr.schema_violations(schema, {"q": "a" * 34 + "!"})
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"regex keyword was evaluated ({elapsed:.1f}s)"
    # The rest of the schema still applies — only the regex keywords are dropped.
    assert violations == []
    assert tr.schema_violations(schema, {"q": 5}) != []


def test_regex_keywords_are_stripped_at_every_depth() -> None:
    nested = {
        "type": "object",
        "properties": {"outer": {"items": [{"pattern": "x"}], "patternProperties": {"^a": {}}}},
    }

    assert tr._without_regex(nested) == {
        "type": "object",
        "properties": {"outer": {"items": [{}]}},
    }


def test_dropping_pattern_properties_also_relaxes_a_closed_object() -> None:
    """The two keywords compose: `patternProperties` is what made those names
    legal, so dropping it under a surviving `additionalProperties: false` turns
    every one of them into a rejected additional property."""
    schema = {
        "type": "object",
        "patternProperties": {"^x-": {"type": "string"}},
        "additionalProperties": False,
    }

    assert tr._without_regex(schema) == {"type": "object"}


def test_an_additional_properties_subschema_is_not_dropped() -> None:
    """Only the `false` form is a closed-object rule. A subschema still
    constrains exactly the values it always did, and must survive."""
    schema = {
        "type": "object",
        "patternProperties": {"^x-": {"type": "string"}},
        "additionalProperties": {"type": "string"},
    }

    assert tr._without_regex(schema) == {"type": "object", "additionalProperties": {"type": "string"}}


def test_a_closed_object_without_pattern_properties_stays_closed() -> None:
    """The relaxation is scoped to the node that lost its pattern rule — it must
    not quietly open every closed object in the schema."""
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
    }

    assert tr._without_regex(schema) == schema


@pytest.mark.asyncio
async def test_a_pattern_property_tool_stays_callable() -> None:
    """End to end: before this, every call to such a tool came back as
    "Additional properties are not allowed" — permanently, for every argument."""
    from contexts.agents.application.runtime.tool_registry import Tool

    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "patternProperties": {"^x-": {"type": "string"}},
        "additionalProperties": False,
        "required": ["name"],
    }
    invoked: list = []

    async def _record(args):
        invoked.append(args)
        return tr.ToolResult(content="ran")

    reg = ToolRegistry([Tool(name="tagger", description="d", input_schema=schema, invoke=_record)])

    res = await reg.call("tagger", {"name": "n", "x-team": "core"})

    assert invoked == [{"name": "n", "x-team": "core"}]
    assert res.is_error is False


@pytest.mark.asyncio
async def test_a_parameter_named_pattern_survives_the_strip() -> None:
    """`pattern` is an ordinary parameter name for a search or glob tool, and MCP
    servers built with the TypeScript SDK emit `additionalProperties: false`.

    Stripping it as though it were the regex keyword dropped the property while
    `required` still listed it, so the tool could not be called either way: supply
    the argument and it is "not allowed", omit it and it is "required". The model
    can satisfy neither.
    """
    from contexts.agents.application.runtime.tool_registry import Tool

    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            # A regex keyword nested inside the property that is *named* `pattern`
            # is still a keyword, and must still go.
            "pattern": {"type": "string", "pattern": "^(a+)+$"},
        },
        "required": ["path", "pattern"],
        "additionalProperties": False,
    }
    invoked: list = []

    async def _record(args):
        invoked.append(args)
        return tr.ToolResult(content="ran")

    reg = ToolRegistry([Tool(name="search_files", description="d", input_schema=schema, invoke=_record)])

    res = await reg.call("search_files", {"path": "/a", "pattern": "a" * 34 + "!"})

    assert not res.is_error, res.content
    assert invoked == [{"path": "/a", "pattern": "a" * 34 + "!"}]
    # And the property is still type-checked.
    assert (await reg.call("search_files", {"path": "/a", "pattern": 5})).is_error


def test_expects_arguments_reads_the_advertised_schema() -> None:
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _ran(_args):
        return tr.ToolResult(content="ran")

    def _tool(name: str, schema: dict):
        return Tool(name=name, description="d", input_schema=schema, invoke=_ran)

    reg = ToolRegistry(
        [
            _tool("none", {"type": "object", "additionalProperties": False}),
            _tool("some", {"type": "object", "properties": {"q": {"type": "string"}}}),
            # The permissive fallback an unprobed MCP binding carries: it accepts
            # arguments, so an empty object there is still ambiguous.
            _tool("permissive", {"type": "object", "additionalProperties": True}),
        ]
    )

    assert reg.expects_arguments("none") is False
    assert reg.expects_arguments("some") is True
    assert reg.expects_arguments("permissive") is True
    # An unknown name falls through to the normal dispatch error, not a rejection.
    assert reg.expects_arguments("nope") is True


def test_instance_data_is_not_rewritten_by_the_strip() -> None:
    """`default`/`enum` hold values, not subschemas; a key named `pattern` in one
    is data the provider is shown, not a regex this validator would run."""
    schema = {"type": "object", "default": {"pattern": "x"}, "enum": [{"pattern": "y"}]}

    assert tr._without_regex(schema) == schema


def test_the_violation_report_is_bounded() -> None:
    """The messages quote the model's own arguments back at it, and a large
    malformed payload would otherwise be echoed into the context window."""
    schema = {
        "type": "object",
        "properties": {f"f{i}": {"type": "integer"} for i in range(50)},
    }
    violations = tr.schema_violations(schema, {f"f{i}": "x" * 400 for i in range(50)})

    assert len(violations) == 50  # the helper reports everything...


@pytest.mark.asyncio
async def test_the_violation_report_sent_to_the_model_is_clipped() -> None:
    # ...and `call` is what bounds what the model is shown.
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _ran(_args):
        return tr.ToolResult(content="ran")

    schema = {"type": "object", "properties": {f"f{i}": {"type": "integer"} for i in range(50)}}
    reg = ToolRegistry([Tool(name="x", description="d", input_schema=schema, invoke=_ran)])

    res = await reg.call("x", {f"f{i}": "x" * 400 for i in range(50)})

    assert res.is_error
    assert len(res.content) <= _MAX_TOOL_OUTPUT
    assert "and 40 more" in res.content


@pytest.mark.asyncio
async def test_registry_first_registration_wins_on_duplicate() -> None:
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _a(_args):
        from contexts.agents.application.runtime.tool_registry import ToolResult

        return ToolResult(content="A")

    async def _b(_args):
        from contexts.agents.application.runtime.tool_registry import ToolResult

        return ToolResult(content="B")

    reg = ToolRegistry(
        [
            Tool(name="dup", description="d", input_schema={}, invoke=_a),
            Tool(name="dup", description="d", input_schema={}, invoke=_b),
        ]
    )
    assert len(reg) == 1
    res = await reg.call("dup", {})
    assert res.content == "A"  # the first registration is kept, the shadow dropped


# --------------------------------------------------------------------------- #
# read_skill (§31)                                                             #
# --------------------------------------------------------------------------- #


async def _read(tool, **args) -> dict:
    res = await tool.invoke(args)
    assert not res.is_error, res.content
    return json.loads(res.content)


def test_read_skill_is_a_reserved_builtin_and_build_registry_builds_it() -> None:
    # AC-14. The `build_agent_tools` drift test cannot see this tool — read_skill is
    # built in tool_registry, not in builtin_tools — so its wiring is asserted here or
    # nowhere. The name matters twice over: BUILTIN_TOOL_NAMES is also what
    # agent_service derives its reserved-name guard from, so a user LOCAL_FUNCTION
    # called read_skill would otherwise shadow the real one.
    assert "read_skill" in BUILTIN_TOOL_NAMES

    reg = build_registry(object(), agent_id=uuid.uuid4(), skills=_snap(make_skill()))

    assert reg.get("read_skill") is not None
    assert "read_skill" in {s["name"] for s in reg.specs()}


@pytest.mark.asyncio
async def test_read_skill_returns_the_body_from_the_snapshot() -> None:
    # AC-5. The tool holds the snapshot; there is no db argument it could query with.
    skill = make_skill(name="pdf-fill", body="# PDF Fill\n\nOpen the form, then fill it.")
    tool = build_read_skill_tool(_snap(skill))

    out = await _read(tool, name="pdf-fill")

    assert out == {"name": "pdf-fill", "body": "# PDF Fill\n\nOpen the form, then fill it."}


@pytest.mark.asyncio
async def test_read_skill_unknown_name_is_a_tool_error_not_a_raise() -> None:
    # AC-5. The model can misread the index; that must cost it a tool round, not the
    # user's turn. `is_error` goes back to the model, an exception aborts the loop.
    tool = build_read_skill_tool(_snap(make_skill(name="pdf-fill")))

    res = await tool.invoke({"name": "no-such-skill"})

    assert res.is_error
    assert "no-such-skill" in res.content
    assert "pdf-fill" in res.content  # names what it could have called instead


@pytest.mark.asyncio
async def test_read_skill_cannot_reach_a_skill_outside_the_snapshot() -> None:
    # The turn-time tap drops a skill whose containment now fails. If read_skill could
    # still resolve it, the tap would be decorative and the drop cosmetic.
    dropped = make_skill(name="secret-skill", body="the other tenant's instructions")
    tool = build_read_skill_tool(_snap(make_skill(name="pdf-fill")))

    res = await tool.invoke({"name": dropped.name})

    assert res.is_error
    assert "the other tenant's instructions" not in res.content


@pytest.mark.asyncio
async def test_read_skill_rejects_an_offset_outside_the_body() -> None:
    tool = build_read_skill_tool(_snap(make_skill(name="pdf-fill", body="short")))

    res = await tool.invoke({"name": "pdf-fill", "offset": 9_999})

    assert res.is_error


_LONG_BODIES = {
    "latin": "The quick brown fox jumps over the lazy dog. " * 4_000,
    # Split mid-CJK-run: CJK costs 1 token/char, so the same budget cuts at a
    # completely different offset — and a cut between two CJK characters must not be
    # smoothed over by the estimator's Latin path.
    "cjk": "把表格打開然後逐欄填寫並存檔。" * 3_000,
    # No surrogate pairs on the Python side, but an astral character is 4 UTF-8 bytes
    # and 1 str index: a byte-based cut would sever it, a character one cannot.
    "astral": "𝕏marks the spot. " * 3_000,
}


@pytest.mark.parametrize("label", list(_LONG_BODIES))
@pytest.mark.asyncio
async def test_read_skill_spans_reassemble_the_body_exactly(label: str) -> None:
    # AC-33. Walk the whole body through continuation calls and rebuild it.
    body = _LONG_BODIES[label]
    tool = build_read_skill_tool(_snap(make_skill(name="long-one", body=body)))

    spans: list[str] = []
    offset: int | None = 0
    seen: set[int] = set()
    while offset is not None:
        assert offset not in seen, "continuation offset repeated — the walk would never end"
        seen.add(offset)
        res = await tool.invoke({"name": "long-one", "offset": offset})
        assert not res.is_error, res.content
        # Measured on what the tool actually hands the loop, not on a re-dump: the
        # clip is applied to that exact string, and a re-dump with different
        # ensure_ascii would measure a string nothing ever sees.
        assert len(res.content) <= _MAX_TOOL_OUTPUT
        assert "[truncated]" not in res.content, "the byte clip severed the JSON"
        out = json.loads(res.content)
        spans.append(out["body"])
        assert estimate_tokens(out["body"]) <= _SKILL_BODY_TOKEN_BUDGET
        offset = out.get("truncated_at_offset")

    assert len(spans) > 1, "the fixture must actually exceed the budget or it proves nothing"
    # No gap, no repeat. Deliberately NOT asserted: that the spans' estimates sum to
    # the whole body's. estimate_tokens is max(1, cjk + latin // 4) — non-additive by
    # construction, so that sum is not a property of correct code.
    assert "".join(spans) == body


@pytest.mark.asyncio
async def test_read_skill_body_within_budget_is_not_truncated() -> None:
    tool = build_read_skill_tool(_snap(make_skill(name="short-one", body="a" * 200)))

    out = await _read(tool, name="short-one")

    assert "truncated_at_offset" not in out
    assert out["body"] == "a" * 200


# --------------------------------------------------------------------------- #
# Phase 2 — the file manifest, the `path` arm, the scan gate, and AC-19        #
# --------------------------------------------------------------------------- #


def _sfile(
    skill_id: uuid.UUID,
    *,
    path: str = "references/guide.md",
    kind: SkillFileKind = SkillFileKind.REFERENCE,
    scan_status: SkillScanStatus = SkillScanStatus.CLEAN,
    size_bytes: int = 12,
) -> SkillFile:
    return SkillFile(
        id=uuid.uuid4(),
        skill_id=skill_id,
        path=path,
        kind=kind,
        mime="text/markdown",
        size_bytes=size_bytes,
        sha256="a" * 64,
        minio_key="k",
        scan_status=scan_status,
        extracted_chars=size_bytes,
        created_at=NOW,
    )


def _many_files(skill_id: uuid.UUID, n: int) -> tuple[SkillFile, ...]:
    return tuple(
        _sfile(skill_id, path=f"references/file-{i:03d}-with-a-fairly-long-name.md") for i in range(n)
    )


class _FileFacade:
    """Stands in for `SkillsFacade` at the seam `_serve_file` imports it through."""

    texts: ClassVar[dict[str, str]] = {}
    asked: ClassVar[list[str]] = []

    def __init__(self, _db: object) -> None:
        pass

    async def read_skill_file_text(self, file: SkillFile) -> str:
        _FileFacade.asked.append(file.path)
        return _FileFacade.texts[file.path]


@pytest.fixture
def file_facade(monkeypatch) -> type[_FileFacade]:
    import contexts.skills.interfaces.facade as facade_mod

    _FileFacade.texts = {}
    _FileFacade.asked = []
    monkeypatch.setattr(facade_mod, "SkillsFacade", _FileFacade)
    return _FileFacade


@pytest.mark.asyncio
async def test_read_skill_lists_its_files(file_facade) -> None:
    # AC-18: the response lists file paths, so the model learns what it may ask for.
    skill = make_skill(name="pdf-fill", body="# Body")
    files = (
        _sfile(skill.id, path="references/guide.md"),
        _sfile(skill.id, path="scripts/run.py", kind=SkillFileKind.SCRIPT),
    )
    tool = build_read_skill_tool(_snap(skill, files={skill.id: files}))

    out = await _read(tool, name="pdf-fill")

    assert out["body"] == "# Body"
    assert out["files"] == [
        {"path": "references/guide.md", "kind": "reference", "size_bytes": 12},
        {"path": "scripts/run.py", "kind": "script", "size_bytes": 12},
    ]


@pytest.mark.asyncio
async def test_a_skill_with_no_files_carries_no_manifest_key(file_facade) -> None:
    # An empty list dressed as data costs tokens on every read and tells the model
    # nothing it could act on.
    skill = make_skill(name="pdf-fill")
    out = await _read(build_read_skill_tool(_snap(skill)), name="pdf-fill")
    assert "files" not in out


@pytest.mark.asyncio
async def test_read_skill_serves_a_reference_files_text(file_facade) -> None:
    # AC-18: reference-file text is readable.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    file_facade.texts["references/guide.md"] = "# Guide\n\nStep one."
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    out = await _read(tool, name="pdf-fill", path="references/guide.md")

    assert out["path"] == "references/guide.md"
    assert out["text"] == "# Guide\n\nStep one."
    # `body` is absent: the model asked for a file, and shipping the body too would
    # double the cost of every file read.
    assert "body" not in out


@pytest.mark.asyncio
async def test_a_file_read_resolves_against_the_snapshot_not_a_query(file_facade) -> None:
    # The facade is handed the snapshot's own SkillFile row. If the tool passed an id
    # instead, the facade would need a lookup — a read primitive over every tenant's
    # files, which is the same reason read_skill never resolves a *name*.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    file_facade.texts["references/guide.md"] = "text"
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    await _read(tool, name="pdf-fill", path="references/guide.md")

    assert file_facade.asked == ["references/guide.md"]


@pytest.mark.asyncio
async def test_an_unknown_path_is_a_tool_error_naming_the_real_ones(file_facade) -> None:
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    res = await tool.invoke({"name": "pdf-fill", "path": "references/nope.md"})

    assert res.is_error
    assert "references/guide.md" in res.content


@pytest.mark.asyncio
async def test_another_skills_file_is_unreachable_by_path(file_facade) -> None:
    # The manifest is per skill, so naming a path that exists on a *different* skill
    # must not resolve — the snapshot is keyed by skill id for this reason.
    mine, theirs = make_skill(name="mine"), make_skill(name="theirs")
    theirs_file = _sfile(theirs.id, path="references/secret.md")
    file_facade.texts["references/secret.md"] = "another tenant's document"
    tool = build_read_skill_tool(_snap(mine, theirs, files={theirs.id: (theirs_file,)}))

    res = await tool.invoke({"name": "mine", "path": "references/secret.md"})

    assert res.is_error
    assert "another tenant's document" not in res.content
    assert file_facade.asked == []


@pytest.mark.parametrize(
    ("kind", "path"),
    # The path matches what `kind_for_path` would derive, so the fixture is a state the
    # system can actually reach — and, deliberately, neither path contains the word the
    # assertion looks for. An earlier version used `scripts/x.asset` and asserted
    # `word in res.content`, which passed off the echoed path: deleting `kind.value` from
    # the message left it green. Probed.
    [(SkillFileKind.SCRIPT, "scripts/run.py"), (SkillFileKind.ASSET, "assets/logo.png")],
)
@pytest.mark.asyncio
async def test_only_reference_files_are_readable_as_text(file_facade, kind, path) -> None:
    # R31.18: a script is staged for the interpreter, an asset is opaque bytes.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path=path, kind=kind)
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    res = await tool.invoke({"name": "pdf-fill", "path": path})

    assert res.is_error
    # The kind must be *named*, not merely implied by the path the caller already sent:
    # the model has to learn why this file is unreadable, not guess.
    assert kind.value in res.content
    assert file_facade.asked == [], "no byte may be fetched for a file that is not text"


@pytest.mark.parametrize(
    "status", [SkillScanStatus.PENDING, SkillScanStatus.SKIPPED, SkillScanStatus.QUARANTINED]
)
@pytest.mark.asyncio
async def test_a_non_clean_file_makes_the_whole_skill_unreadable(file_facade, status) -> None:
    # AC-34 — including the *body*, not just the offending file: Q-18's rule is that a
    # skill is one semantic unit, and a body whose references cannot be fetched induces
    # confabulation.
    skill = make_skill(name="pdf-fill", body="secret instructions")
    f = _sfile(skill.id, path="assets/x.bin", kind=SkillFileKind.ASSET, scan_status=status)
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    res = await tool.invoke({"name": "pdf-fill"})

    assert res.is_error
    assert "assets/x.bin" in res.content
    assert "secret instructions" not in res.content
    # The error tells the model not to invent the contents — the failure mode AC-34
    # exists to prevent is confabulation, not disclosure.
    assert "Do not guess" in res.content


@pytest.mark.asyncio
async def test_the_scan_gate_also_blocks_the_file_arm(file_facade) -> None:
    skill = make_skill(name="pdf-fill")
    bad = _sfile(skill.id, path="references/guide.md", scan_status=SkillScanStatus.QUARANTINED)
    file_facade.texts["references/guide.md"] = "infected text"
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (bad,)}))

    res = await tool.invoke({"name": "pdf-fill", "path": "references/guide.md"})

    assert res.is_error
    assert file_facade.asked == [], "the gate must fire before any byte is fetched"


@pytest.mark.asyncio
async def test_a_clean_skill_is_unaffected_by_another_skills_quarantine(file_facade) -> None:
    # The gate is per skill. One poisoned skill must not take the agent's others down.
    good, bad = make_skill(name="good", body="fine"), make_skill(name="bad")
    tool = build_read_skill_tool(
        _snap(
            good,
            bad,
            files={
                good.id: (_sfile(good.id, path="references/a.md"),),
                bad.id: (_sfile(bad.id, path="references/b.md", scan_status=SkillScanStatus.QUARANTINED),),
            },
        )
    )

    out = await _read(tool, name="good")
    assert out["body"] == "fine"
    assert (await tool.invoke({"name": "bad"})).is_error


@pytest.mark.asyncio
async def test_a_body_read_is_recorded_for_message_metadata(file_facade) -> None:
    # AC-19 / R31.17. body_sha256 is the load-bearing field: bodies are mutable in place
    # with no version tree, so `version` says which row was read and only the hash says
    # which bytes ran.
    skill = make_skill(name="pdf-fill", body="# Body")
    reads: list[SkillRead] = []
    tool = build_read_skill_tool(_snap(skill), reads=reads)

    await _read(tool, name="pdf-fill")

    assert len(reads) == 1
    assert reads[0].to_dict() == {
        "skill_id": str(skill.id),
        "name": "pdf-fill",
        "scope": skill.scope.value,
        "version": skill.version,
        "body_sha256": skill.body_sha256,
    }


@pytest.mark.asyncio
async def test_every_body_read_is_recorded_including_continuations(file_facade) -> None:
    # Two reads of one skill are two records: the question "which bytes ran" is asked of
    # a turn, and a body edited between two reads would otherwise be invisible.
    skill = make_skill(name="pdf-fill", body="# Body")
    reads: list[SkillRead] = []
    tool = build_read_skill_tool(_snap(skill), reads=reads)

    await _read(tool, name="pdf-fill")
    await _read(tool, name="pdf-fill")

    assert len(reads) == 2


@pytest.mark.asyncio
async def test_a_failed_read_records_nothing(file_facade) -> None:
    # An unknown name and a blocked skill both served no bytes, so neither is a read.
    skill = make_skill(name="pdf-fill")
    blocked = make_skill(name="blocked")
    reads: list[SkillRead] = []
    tool = build_read_skill_tool(
        _snap(
            skill,
            blocked,
            files={blocked.id: (_sfile(blocked.id, scan_status=SkillScanStatus.QUARANTINED),)},
        ),
        reads=reads,
    )

    await tool.invoke({"name": "no-such-skill"})
    await tool.invoke({"name": "blocked"})
    await tool.invoke({"name": "pdf-fill", "offset": 99_999})

    assert reads == []


@pytest.mark.asyncio
async def test_a_file_read_is_not_recorded(file_facade) -> None:
    # R31.17 names the *body* hash. A file read is already pinned by the body read that
    # had to precede it to learn the path.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    file_facade.texts["references/guide.md"] = "text"
    reads: list[SkillRead] = []
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}), reads=reads)

    await _read(tool, name="pdf-fill", path="references/guide.md")

    assert reads == []


@pytest.mark.asyncio
async def test_a_long_file_reassembles_across_continuations(file_facade) -> None:
    # AC-33's contract holds on the file arm too: the span/offset walk is the same code.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    text = "The quick brown fox jumps over the lazy dog. " * 4_000
    file_facade.texts["references/guide.md"] = text
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    spans: list[str] = []
    offset: int | None = 0
    while offset is not None:
        res = await tool.invoke({"name": "pdf-fill", "path": "references/guide.md", "offset": offset})
        assert not res.is_error, res.content
        assert len(res.content) <= _MAX_TOOL_OUTPUT
        assert "[truncated]" not in res.content, "the byte clip severed the JSON"
        out = json.loads(res.content)
        spans.append(out["text"])
        offset = out.get("truncated_at_offset")

    assert len(spans) > 1
    assert "".join(spans) == text


@pytest.mark.asyncio
async def test_file_text_is_fetched_once_per_turn(file_facade) -> None:
    # R31.16 — "bodies fetched within a turn are cached for that turn only". The body gets
    # that free by living in the snapshot; file text is fetched on demand, so without a
    # cache a long file is re-fetched and re-parsed once per continuation span.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    file_facade.texts["references/guide.md"] = "The quick brown fox. " * 4_000
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    offset: int | None = 0
    spans = 0
    while offset is not None:
        out = await _read(tool, name="pdf-fill", path="references/guide.md", offset=offset)
        spans += 1
        offset = out.get("truncated_at_offset")

    assert spans > 1, "the fixture must actually span or it proves nothing"
    assert file_facade.asked == ["references/guide.md"], "re-fetched once per span"


@pytest.mark.asyncio
async def test_an_edit_mid_turn_cannot_move_the_ground_under_a_continuation(file_facade) -> None:
    # The real reason the cache is correctness and not speed. An offset is computed
    # against the text the previous call returned; if the next call re-read the file and
    # got different bytes, that offset would index into a different document and AC-33's
    # spans would stop reassembling. R31.16 puts edits on the next turn precisely here.
    skill = make_skill(name="pdf-fill")
    f = _sfile(skill.id, path="references/guide.md")
    original = "The quick brown fox. " * 4_000
    file_facade.texts["references/guide.md"] = original
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (f,)}))

    first = await _read(tool, name="pdf-fill", path="references/guide.md")
    assert first.get("truncated_at_offset")

    # Someone edits the file between the two calls.
    file_facade.texts["references/guide.md"] = "totally different and much shorter"

    spans = [first["text"]]
    offset = first["truncated_at_offset"]
    while offset is not None:
        out = await _read(tool, name="pdf-fill", path="references/guide.md", offset=offset)
        spans.append(out["text"])
        offset = out.get("truncated_at_offset")

    # The turn reassembles the document it started reading, not a splice of two.
    assert "".join(spans) == original


@pytest.mark.asyncio
async def test_two_files_are_cached_independently(file_facade) -> None:
    skill = make_skill(name="pdf-fill")
    a = _sfile(skill.id, path="references/a.md")
    b = _sfile(skill.id, path="references/b.md")
    file_facade.texts = {"references/a.md": "AAA", "references/b.md": "BBB"}
    tool = build_read_skill_tool(_snap(skill, files={skill.id: (a, b)}))

    assert (await _read(tool, name="pdf-fill", path="references/a.md"))["text"] == "AAA"
    assert (await _read(tool, name="pdf-fill", path="references/b.md"))["text"] == "BBB"
    assert (await _read(tool, name="pdf-fill", path="references/a.md"))["text"] == "AAA"
    assert file_facade.asked == ["references/a.md", "references/b.md"]


@pytest.mark.asyncio
async def test_the_manifest_is_counted_in_the_span_budget(file_facade) -> None:
    # The manifest rides in the same JSON as the body, so a long one must shrink the
    # body's span rather than push the rendered result past _MAX_TOOL_OUTPUT — where the
    # byte clip would sever the JSON carrying truncated_at_offset (D-13).
    skill = make_skill(name="long-one", body="x" * 100_000)
    tool = build_read_skill_tool(_snap(skill, files={skill.id: _many_files(skill.id, 60)}))

    res = await tool.invoke({"name": "long-one"})

    assert not res.is_error
    assert len(res.content) <= _MAX_TOOL_OUTPUT
    assert "[truncated]" not in res.content
    out = json.loads(res.content)
    assert len(out["files"]) == 60
    assert out["truncated_at_offset"] > 0


@pytest.mark.parametrize("n", [1, 60, 120, 220, 400])
@pytest.mark.asyncio
async def test_the_result_is_always_parseable_however_many_files(file_facade, n: int) -> None:
    # Found by this task's quality gate and reproduced before it was fixed: nothing caps
    # files per skill (Q-17's 500-entry limit is a *bundle* rule, and bundles are Phase
    # 4), and a path may be 255 chars — so a manifest can exceed _MAX_TOOL_OUTPUT on its
    # own. `_fit_skill_body`'s "lo fits" invariant was then false at lo=0, it returned a
    # 1-char span regardless, and the clip severed the JSON holding truncated_at_offset.
    # The model got unparseable output and an offset advancing one character per call,
    # burning all MAX_TOOL_ROUNDS on garbage.
    #
    # The first version of this test used 60 files and rendered 15 996 bytes — four under
    # the cap. That is why this one sweeps the count instead of picking one.
    skill = make_skill(name="long-one", body="x" * 100_000)
    tool = build_read_skill_tool(_snap(skill, files={skill.id: _many_files(skill.id, n)}))

    res = await tool.invoke({"name": "long-one"})

    assert not res.is_error
    assert len(res.content) <= _MAX_TOOL_OUTPUT
    assert "[truncated]" not in res.content, "the byte clip severed the JSON"
    out = json.loads(res.content)  # the assertion that actually failed before the fix
    # And the span must make real progress, or the continuation walk never terminates.
    assert len(out["body"]) >= 1
    assert out["truncated_at_offset"] >= len(out["body"])


@pytest.mark.asyncio
async def test_an_over_large_manifest_is_trimmed_and_says_so(file_facade) -> None:
    # Trimming beats refusing: the body is what was asked for. But the model must be told
    # the list is partial, or it concludes the absent files do not exist — the
    # confabulation Q-18 is written against, one level down.
    skill = make_skill(name="long-one", body="x" * 100_000)
    tool = build_read_skill_tool(_snap(skill, files={skill.id: _many_files(skill.id, 400)}))

    out = json.loads((await tool.invoke({"name": "long-one"})).content)

    assert out["files_omitted"] > 0
    assert len(out["files"]) + out["files_omitted"] == 400


@pytest.mark.asyncio
async def test_a_manifest_that_fits_reports_nothing_omitted(file_facade) -> None:
    # The key must be absent, not zero: `files_omitted: 0` on every ordinary read is a
    # constant dressed as data.
    skill = make_skill(name="pdf-fill", body="short")
    tool = build_read_skill_tool(_snap(skill, files={skill.id: _many_files(skill.id, 3)}))

    out = await _read(tool, name="pdf-fill")

    assert "files_omitted" not in out
    assert len(out["files"]) == 3


@pytest.mark.asyncio
async def test_an_unknown_name_error_is_clipped_like_any_other_output(file_facade) -> None:
    # Found by review. The success path is bounded to _MAX_TOOL_OUTPUT by construction;
    # the error path interpolated every bound skill's name and skipped the clip. FU-24
    # notes ~1700 skills fit under the 3000-token index cap (an index line is ~2 tokens),
    # so this branch could push ~110KB into the model's context — and the tool loop runs
    # up to MAX_TOOL_ROUNDS times.
    many = [make_skill(name=f"skill-{i:04d}-with-a-fairly-long-name") for i in range(2_000)]
    tool = build_read_skill_tool(_snap(*many))

    res = await tool.invoke({"name": "no-such-skill"})

    assert res.is_error
    assert len(res.content) <= _MAX_TOOL_OUTPUT + len("\n…[truncated]")


@pytest.mark.asyncio
async def test_an_unknown_path_error_is_clipped(file_facade) -> None:
    # The same hole one level down, bounded by MAX_SKILL_FILES rather than the index cap.
    skill = make_skill(name="pdf-fill")
    tool = build_read_skill_tool(_snap(skill, files={skill.id: _many_files(skill.id, 500)}))

    res = await tool.invoke({"name": "pdf-fill", "path": "references/nope.md"})

    assert res.is_error
    assert len(res.content) <= _MAX_TOOL_OUTPUT + len("\n…[truncated]")


@pytest.mark.asyncio
async def test_a_short_error_is_not_padded_or_mangled(file_facade) -> None:
    # The clip must be a no-op below the cap — a backstop, not a transform.
    tool = build_read_skill_tool(_snap(make_skill(name="pdf-fill")))
    res = await tool.invoke({"name": "nope"})
    assert res.content == "Unknown skill 'nope'. Bound skills: pdf-fill."


@pytest.mark.asyncio
async def test_the_manifest_is_trimmed_once_per_turn_not_once_per_span(file_facade) -> None:
    # `_fit_manifest` re-renders the payload once per popped entry, and its inputs cannot
    # change between two spans of one body — so recomputing it per continuation was
    # O(spans x files^2) for an answer that never moves.
    skill = make_skill(name="long-one", body="x" * 100_000)
    tool = build_read_skill_tool(_snap(skill, files={skill.id: _many_files(skill.id, 300)}))

    calls: list[int] = []
    real = tr._fit_manifest

    def _counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tr, "_fit_manifest", _counting)
        offset: int | None = 0
        spans = 0
        while offset is not None:
            out = await _read(tool, name="long-one", offset=offset)
            spans += 1
            offset = out.get("truncated_at_offset")

    assert spans > 1, "the fixture must actually span or it proves nothing"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_huge_manifest_still_lets_a_file_be_read(file_facade) -> None:
    # The file arm renders `path` rather than the manifest, so it has room — but it runs
    # the same search, and a skill with 400 files is exactly the one whose files someone
    # needs to read.
    skill = make_skill(name="long-one")
    files = _many_files(skill.id, 400)
    file_facade.texts[files[0].path] = "the contents"
    tool = build_read_skill_tool(_snap(skill, files={skill.id: files}))

    out = await _read(tool, name="long-one", path=files[0].path)

    assert out["text"] == "the contents"
