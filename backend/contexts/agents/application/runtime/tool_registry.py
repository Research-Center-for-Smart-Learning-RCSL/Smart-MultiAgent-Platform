"""Per-turn tool registry (K.2).

There is no shared tool abstraction in the codebase — each built-in tool has a
bespoke shape — so this module defines the uniform surface the turn engine's
tool-use loop needs: ``Tool{name, description, input_schema, invoke(args)}`` and
a ``ToolRegistry`` that exposes provider-neutral specs and dispatches calls.

Wired here (no external infra): ``update_wakeup`` (R15.06 — clamp + audit happen
inside ``WakeupService``) and ``read_skill`` (§31 — served entirely from the
turn's already-resolved snapshot, so it needs no DB either). ``web_search`` /
``file`` / ``code_exec`` are injected as ``extra`` tools by their own wiring
(web-search DI; sandbox lands in K.5) so this module stays infra-free.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from contexts.skills.domain.errors import SkillUnreadable
from contexts.skills.domain.models import Skill, SkillFile, SkillFileKind, SkillRead
from contexts.skills.domain.readability import assert_readable
from shared_kernel.tokens import estimate_tokens

if TYPE_CHECKING:
    from contexts.skills.application.binding_service import BoundSet

ToolInvoke = Callable[[dict[str, Any]], Awaitable["ToolResult"]]

# Tells an infrastructure fault apart from a tool failure (see ``ToolRegistry.call``).
# Injected rather than imported so this module keeps the no-infrastructure claim its
# docstring makes: the classification needs ``sqlalchemy.exc``, and the turn engine —
# which owns the session the tools write to — is the layer that knows that.
InfraErrorCheck = Callable[[BaseException], bool]

# Per-tool output cap so a chatty tool can't blow the context window. It lives here
# rather than beside its callers in ``builtin_tools`` because it is the registry's
# contract with the turn loop, and ``read_skill`` — built in this module — has to size
# its own span against it (see :func:`_fit_skill_body`).
_MAX_TOOL_OUTPUT = 16_000

# Largest code_exec artifact the platform will carry into a room. Matches the
# single-shot attachment limit (`app/api/v1/attachments.py`). It lives here, not
# beside either user, because two of them need it: `turn_engine` enforces it when
# fetching, and `builtin_tools` quotes it to the model. Declared twice, the two
# drift and the model is told a limit that is not the one applied.
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

# Per-turn ceilings; the per-file limit above bounds one artifact and nothing
# bounded the set. Both are enforced twice, and both checks must read
# `running + candidate > limit`. See docs/agent-tools/G-artifact-transport.md G.3.
MAX_ARTIFACTS_PER_TURN = 20
MAX_ARTIFACT_TOTAL_BYTES = 64 * 1024 * 1024

# Marks a descriptor that will not be delivered, with the reason. Read by the
# model-facing note, the operator log and the acquisition path, so that a bound
# can never be added without a signal (G.4).
ARTIFACT_SKIP_KEY = "_smap_skip"


def artifact_size_bytes(art: Mapping[str, Any]) -> int:
    """``size_bytes`` off an artifact descriptor. Total, never raises.

    The field is agent-controlled, and a bare ``int()`` on it discards the whole
    batch. See docs/agent-tools/G-artifact-transport.md G.6.
    """
    try:
        size = int(art.get("size_bytes") or 0)
    except (TypeError, ValueError):
        return 0
    return max(size, 0)


def clip_tool_output(text: str, *, suffix: str = "") -> str:
    """The character-level backstop every tool's output passes through.

    *suffix* is appended here rather than by the caller so that the bound stays
    the function's own guarantee. Appending afterwards puts the suffix outside
    the backstop; concatenating before it lets a chatty stdout truncate the
    suffix away. Both matter for `code_exec`'s artifact note (G.4).
    """
    # The suffix cannot outgrow the whole budget, or a caller could evict the
    # output entirely and still exceed the cap this function exists to hold.
    suffix = suffix[:_MAX_TOOL_OUTPUT]
    budget = max(_MAX_TOOL_OUTPUT - len(suffix), 0)
    clipped = text if len(text) <= budget else text[:budget] + "\n…[truncated]"
    return clipped + suffix


def _tool_error(content: str) -> ToolResult:
    """An error result, clipped like every other tool output.

    Exists because the clip is easy to honour on the path you are thinking about and easy
    to forget on the one you are not. `read_skill`'s error branches interpolate lists whose
    length is bounded by nothing this module controls — every bound skill's name, every
    file path on a skill — so the *error* path could overrun `_MAX_TOOL_OUTPUT` while the
    success path was bounded to it by construction. Routing every error through here makes
    the cap structural rather than remembered.

    Not applied in `ToolRegistry.call` instead, which would look like the tidier place:
    `builtin_tools` already clips its own output, and clipping a clipped string re-cuts it
    and mangles the marker.
    """
    return ToolResult(content=clip_tool_output(content), is_error=True)


# Canonical set of built-in / runtime tool names a user LOCAL_FUNCTION must not
# shadow. Single source of truth: agent_service derives its reserved-name guard
# from this, and a drift test (test_builtin_tools_wiring) asserts every hosted
# built-in tool actually built carries a name listed here — so adding a new
# built-in without reserving it fails CI rather than silently allowing a user
# function to shadow it. MCP tools occupy the separate `mcp__` prefix.
BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "update_wakeup",
        "cast_approval_vote",
        "read_skill",
        "web_search",
        "code_exec",
        "file",
        "file_search",
        # Delegated activity control ([R30.37]). These two are the only names here
        # that no `agent_tools` row can produce — they come from a per-room grant
        # instead — but they are reserved on exactly the same terms, because what
        # this set bounds is the runtime namespace, not the configuration table.
        "start_activity",
        "end_activity",
    }
)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool hands back to the model (text), plus an error flag."""

    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: ToolInvoke

    def spec(self) -> dict[str, Any]:
        """Provider-neutral tool definition the adapters translate (K.1)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


logger = logging.getLogger(__name__)

# How many schema violations are named back to the model. All of them can be one
# message per array element of a large malformed payload, and the whole thing is
# echoed into the context window.
_MAX_REPORTED_VIOLATIONS = 10


# Regex keywords, dropped before validating. A tool's `input_schema` is not ours:
# a LOCAL_FUNCTION carries whatever `parameters` its author wrote, and an MCP
# binding carries whatever its server returned from the capture probe. Executing an
# attacker-chosen regex against model-written arguments is catastrophic
# backtracking on demand — measured at 45s of un-preemptible CPU for `^(a+)+$`
# against 30 characters, which blocks the worker's whole event loop, not just the
# offending turn. No built-in schema uses them, and the provider still receives the
# full schema for constrained decoding; only this local copy is stripped.
_UNSAFE_SCHEMA_KEYWORDS = frozenset({"pattern", "patternProperties"})

# Keywords whose value is a map of *property names* to subschemas. Their keys are
# author-chosen names, not schema keywords, so the strip must not read them as such
# — `pattern` is an ordinary parameter name for a search or glob tool, and dropping
# it while `required` still lists it leaves a tool the model can never call.
_NAME_KEYED_SUBSCHEMAS = frozenset({"properties", "$defs", "definitions", "dependentSchemas"})

# Keywords whose value is instance data rather than a subschema. Recursing into them
# would rewrite a default or an enum member that happens to contain these keys.
_INSTANCE_VALUED_KEYWORDS = frozenset({"enum", "const", "default", "examples"})


def _without_regex(node: Any) -> Any:
    """``node`` with the regex keywords removed, in keyword position only.

    Dropping ``patternProperties`` also drops a sibling ``additionalProperties:
    false``. The two compose: the pattern is what made those property names
    legal, so removing it while the closed-object rule stands turns every one of
    them into a rejected "additional property" and the tool becomes permanently
    uncallable — a hard fail, where this module's posture for a third-party
    schema it cannot use is to fail open (see :func:`schema_violations`). Only
    the ``false`` form is dropped; an ``additionalProperties`` subschema still
    constrains the same values it always did.
    """
    if isinstance(node, dict):
        drops_pattern_properties = "patternProperties" in node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSAFE_SCHEMA_KEYWORDS:
                continue
            if key == "additionalProperties" and value is False and drops_pattern_properties:
                continue
            if key in _INSTANCE_VALUED_KEYWORDS:
                out[key] = value
            elif key in _NAME_KEYED_SUBSCHEMAS and isinstance(value, dict):
                out[key] = {name: _without_regex(sub) for name, sub in value.items()}
            else:
                out[key] = _without_regex(value)
        return out
    if isinstance(node, list):
        return [_without_regex(v) for v in node]
    return node


def schema_violations(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """Every way ``args`` breaks ``schema`` (empty = valid), regex keywords aside.

    Deliberately a copy of `contexts/activities/.../validators/schema.py`'s
    `payload_errors` rather than an import: that helper belongs to the activities
    context, and a cross-context import for four lines would buy a coupling worth
    more than the duplication.

    Fails **open** on anything the validator cannot handle — a malformed schema, or
    a `$ref` jsonschema refuses to resolve. Built-in schemas are held well-formed by
    a drift test, but an MCP server's captured contract is written by someone else
    and an unusable one must cost the agent its validation, not its turn.
    """
    try:
        validator = Draft202012Validator(_without_regex(schema))
        return [err.message for err in validator.iter_errors(args)]
    except Exception:
        logger.warning("tool input_schema is not usable; skipping validation", exc_info=True)
        return []


class ToolRegistry:
    """Name → Tool table for one turn. Dispatch raises only on infrastructure."""

    def __init__(self, tools: list[Tool], *, is_infra_error: InfraErrorCheck | None = None) -> None:
        # A duplicate name would silently shadow a built-in (last-wins dict),
        # so the first registration wins and any collision is dropped + logged.
        # Reserved-name validation upstream should make this unreachable; this is
        # the backstop.
        self._by_name: dict[str, Tool] = {}
        for t in tools:
            if t.name in self._by_name:
                logger.warning("duplicate tool name %r ignored (first registration kept)", t.name)
                continue
            self._by_name[t.name] = t
        # Absent a check, nothing is infrastructure and every failure degrades —
        # the behaviour before this seam existed.
        self._is_infra_error = is_infra_error

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._by_name.values()]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def expects_arguments(self, name: str) -> bool:
        """Does this tool declare any parameters at all?

        The turn loop asks before refusing an empty argument object as truncated:
        for a tool that takes none, `{}` is complete by contract and cannot have
        been cut off, so refusing it would hand the model advice — "retry with a
        smaller payload" — that it has no way to act on. Unknown names answer True
        so an unknown tool still goes through the normal dispatch error.
        """
        tool = self._by_name.get(name)
        if tool is None:
            return True
        schema = tool.input_schema or {}
        # `additionalProperties: true` with no declared properties is the
        # permissive fallback an unprobed MCP binding carries — it accepts
        # arguments, so an empty object there is still ambiguous.
        return bool(schema.get("properties")) or schema.get("additionalProperties") is not False

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Dispatch one tool call, degrading *domain* failures into a result.

        An unknown tool or a tool that raised on its own terms is something the
        model can act on — a different tool, different arguments, or an apology —
        so it comes back as ``is_error`` content and the turn continues.

        An **infrastructure** fault is not. It is not a fact about the tool, the
        model cannot route around it, and on a shared session it has already made
        every later write fail — so continuing burns provider spend on a turn whose
        reply can no longer be persisted. Those propagate and fail the turn now.
        See docs/tasks/2026-07-22-tool-dispatch-failure-categories §3 Q-4.

        The exception itself never reaches the model: a SQLAlchemy message can carry
        the failing SQL, table names and parameter values.

        A **protocol violation** — arguments that do not satisfy the schema the tool
        advertised — is rejected here, before ``invoke``. Nothing downstream did
        that: every tool coerced instead, so a missing required field became a
        default and the model was told the call succeeded. `file` op=write with no
        `content` truncated its target and reported `written: 0` without an error.
        """
        tool = self._by_name.get(name)
        if tool is None:
            return ToolResult(content=f"Unknown tool {name!r}.", is_error=True)
        violations = schema_violations(tool.input_schema, args)
        if violations:
            shown = violations[:_MAX_REPORTED_VIOLATIONS]
            more = len(violations) - len(shown)
            detail = "; ".join(shown) + (f"; and {more} more" if more else "")
            # `_tool_error` clips: the messages quote the model's own arguments
            # back at it, and a large malformed payload would otherwise be echoed
            # into the context window in full.
            return _tool_error(f"Tool {name!r} was not run — invalid arguments: {detail}")
        try:
            return await tool.invoke(args)
        except Exception as exc:
            if self._is_infra_error is not None and self._is_infra_error(exc):
                # Logged here, where the classification is made, so the resulting
                # rise in failed turns is attributable to this decision.
                logger.error(
                    "tool %r hit an infrastructure fault (%s); failing the turn",
                    name,
                    type(exc).__name__,
                    exc_info=True,
                )
                raise
            return ToolResult(content=f"Tool {name!r} failed: {exc}", is_error=True)

    def __len__(self) -> int:
        return len(self._by_name)

    def __bool__(self) -> bool:
        return bool(self._by_name)


# --------------------------------------------------------------------------- #
# Built-in tool builders                                                       #
# --------------------------------------------------------------------------- #

_UPDATE_WAKEUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "every_n_messages": {
            "type": "integer",
            "description": "Fire a wake-up after every N user messages (clamped to 1..1000).",
        },
        "silence_minutes": {
            "type": "integer",
            "description": "Fire after T minutes of silence (clamped to 1..1440).",
        },
    },
    "additionalProperties": False,
}


def build_update_wakeup_tool(db: Any, *, agent_id: uuid.UUID) -> Tool:
    """R15.06 — agent self-adjusts its own wake-up cadence (hard/soft clamp R15.07)."""

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        cfg = await OrchestrationFacade(db).update_wakeup(
            agent_id=agent_id,
            every_n_messages=_opt_int(args.get("every_n_messages")),
            silence_minutes=_opt_int(args.get("silence_minutes")),
            actor_agent_id=agent_id,
        )
        return ToolResult(content=json.dumps(cfg.to_dict()))

    return Tool(
        name="update_wakeup",
        description=(
            "Adjust your own wake-up triggers: how many user messages or how many "
            "minutes of silence should fire your next turn. Values are clamped to "
            "safe bounds."
        ),
        input_schema=_UPDATE_WAKEUP_SCHEMA,
        invoke=_invoke,
    )


_CAST_APPROVAL_VOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_id": {
            "type": "string",
            "description": "The approval gate id you were notified about.",
        },
        "vote": {
            "type": "boolean",
            "description": "true to approve, false to reject.",
        },
        "rationale": {
            "type": "string",
            "description": "Optional short reason for your vote.",
        },
    },
    "required": ["approval_id", "vote"],
    "additionalProperties": False,
}


def build_cast_approval_vote_tool(
    db: Any,
    *,
    agent_id: uuid.UUID,
    allowed_approvals: dict[uuid.UUID, uuid.UUID | None],
    voted: set[uuid.UUID] | None = None,
) -> Tool:
    """R15.10–R15.14 — let an approver agent vote on a gate it was notified of.

    Scoped to the keys of ``allowed_approvals`` (the gates whose notifications
    this turn drained) so an agent cannot vote on an arbitrary gate id it
    guessed. The mapped value carries each gate's originating ``chatroom_id`` (or
    None for a headless/workflow gate) so resolution publishes ``approval.resolved``
    on the right room channel.

    ``voted`` (F-29) — a mutable sink the caller reads after the turn to tell
    a cast ballot apart from a drained-but-unacted-on one (`turn_engine.py`'s
    ``_settle_pending_approvals``), mirroring the ``artifact_sink`` precedent."""

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        raw = str(args.get("approval_id", ""))
        try:
            approval_id = uuid.UUID(raw)
        except ValueError:
            return ToolResult(content=f"Invalid approval_id {raw!r}.", is_error=True)
        if approval_id not in allowed_approvals:
            return ToolResult(
                content=f"approval_id {raw} is not one you were asked to vote on.",
                is_error=True,
            )
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        ballot = await OrchestrationFacade(db).cast_approval_vote(
            approval_id=approval_id,
            voter_agent_id=agent_id,
            vote=bool(args.get("vote")),
            rationale=(str(args["rationale"]) if args.get("rationale") else None),
            chatroom_id=allowed_approvals[approval_id],
        )
        if voted is not None:
            voted.add(approval_id)
        return ToolResult(content=json.dumps({"approval_id": raw, "vote": ballot.vote, "recorded": True}))

    return Tool(
        name="cast_approval_vote",
        description=(
            "Cast your approval vote on a gate you were notified about. Provide the "
            "approval_id from the notification, a boolean vote, and an optional rationale."
        ),
        input_schema=_CAST_APPROVAL_VOTE_SCHEMA,
        invoke=_invoke,
    )


_READ_SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The skill's name, exactly as it appears in the skills index.",
        },
        "path": {
            "type": "string",
            "description": (
                "Optional. Omit to read the skill's instructions and its file list. Pass "
                "one of the reference paths from that list to read that file instead."
            ),
        },
        "offset": {
            "type": "integer",
            "description": (
                "Resume reading at this character offset. Pass the truncated_at_offset "
                "returned by a previous call to read the next span of a long skill."
            ),
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}

# R31.15 / AC-33. A fixed per-call allowance, not "whatever is left of the window":
# `build_registry` runs before the request is assembled at all — and the request is
# rebuilt on the recompaction path — so any window measured here would be stale by
# construction. This does not fix the unbudgeted mid-tool-loop growth of FU-4 and does
# not claim to; it only keeps one skill body from being the thing that blows the window.
_SKILL_BODY_TOKEN_BUDGET = 8000


def _fit_skill_body(
    name: str,
    body: str,
    offset: int,
    *,
    files: Sequence[SkillFile] = (),
    path: str | None = None,
    files_omitted: int = 0,
) -> tuple[str, int | None]:
    """The longest span of ``body[offset:]`` that fits, and where to resume after it.

    Two bounds, both non-decreasing in the span's length, so one binary search settles
    both: ``_SKILL_BODY_TOKEN_BUDGET`` against the estimate, and ``_MAX_TOOL_OUTPUT``
    against the *rendered* result. The second bound is not redundant with
    :func:`clip_tool_output` — it is what keeps the clip a no-op here. The clip cuts
    bytes, and a result severed mid-JSON would strand ``truncated_at_offset`` inside
    the string it was meant to index, which is exactly the contract AC-33 rests on.

    The offset is a **character** offset because it cannot be a token one:
    ``estimate_tokens`` is ``max(1, cjk + latin // 4)`` — non-additive, and with no
    inverse to seek by. Returns ``(span, next_offset)``; ``next_offset`` is None when
    the span reaches the end of the body.
    """
    rest = body[offset:]
    # Probed against the longest offset the payload could ever carry, so the real
    # result is never longer than what the search measured. `files` and `path` are
    # included because they are part of that payload: measuring an envelope without the
    # manifest would let the rendered result overrun `_MAX_TOOL_OUTPUT` by the manifest's
    # own length, and the byte clip would then sever the JSON that carries the offset —
    # the exact failure D-13 records.
    envelope = len(
        _read_skill_payload(name, "", len(body), files=files, path=path, files_omitted=files_omitted)
    )

    def fits(span: str) -> bool:
        return (
            estimate_tokens(span) <= _SKILL_BODY_TOKEN_BUDGET
            and envelope + len(json.dumps(span, ensure_ascii=False)) <= _MAX_TOOL_OUTPUT
        )

    if fits(rest):
        return rest, None
    lo, hi = 0, len(rest)  # invariant: lo fits, hi does not
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(rest[:mid]):
            lo = mid
        else:
            hi = mid
    # Unreachable at these constants (one character always fits), but a zero-length
    # span would hand the model a continuation offset it has already read from, and it
    # would call forever.
    lo = max(lo, 1)
    return rest[:lo], offset + lo


def _read_skill_payload(
    name: str,
    span: str,
    next_offset: int | None,
    *,
    files: Sequence[SkillFile] = (),
    path: str | None = None,
    files_omitted: int = 0,
) -> str:
    payload: dict[str, Any] = {"name": name}
    if path is not None:
        payload["path"] = path
    payload["body" if path is None else "text"] = span
    if next_offset is not None:
        payload["truncated_at_offset"] = next_offset
    if files:
        # AC-18's manifest. Paths are safe to render because `skill_file_path_reason`
        # rejected newlines and the index delimiter at every entry point — this is the
        # second channel into model context the path rule exists for (§8 threat 8).
        payload["files"] = [{"path": f.path, "kind": f.kind.value, "size_bytes": f.size_bytes} for f in files]
    if files_omitted:
        payload["files_omitted"] = files_omitted
    return json.dumps(payload, ensure_ascii=False)


# The span every result is guaranteed room for. Its job is to make `_fit_skill_body`'s
# "lo fits" invariant true by construction: the manifest is trimmed until an empty span
# plus this much content still renders under `_MAX_TOOL_OUTPUT`, so the binary search can
# never be handed a problem with no solution.
_MIN_SPAN_CHARS = 512


def _fit_manifest(
    name: str,
    body_len: int,
    files: Sequence[SkillFile],
) -> tuple[list[SkillFile], int]:
    """The longest prefix of `files` that still leaves room to serve content.

    Nothing caps files per skill — Q-17's 500-entry limit is a *bundle* rule, and bundles
    are Phase 4 — and a path may be 255 characters, so a manifest can exceed
    `_MAX_TOOL_OUTPUT` on its own. When it did, `_fit_skill_body`'s stated invariant
    ("lo fits") was false at `lo = 0`: the search returned a one-character span anyway,
    the byte clip severed the JSON carrying `truncated_at_offset`, and the model got
    unparseable output plus an offset advancing one character per call — burning all
    `MAX_TOOL_ROUNDS`. That is D-13's failure arriving through the manifest instead of the
    body. Reproduced at 220 files; 60 files rendered 15 996 bytes, four under the cap,
    which is exactly why the first test of this missed it.

    Trimming the manifest rather than refusing the read is the lesser harm: the body is
    what the model asked for, and `files_omitted` tells it the list is partial so it does
    not conclude the absent files do not exist. An omitted file is still *readable* — the
    `path` arm resolves against the whole snapshot, not the shown prefix — so this hides
    paths, never content. Returns `(kept, omitted_count)`.

    No `path` parameter: the file arm renders `path` instead of a manifest, so it has
    nothing to trim and never calls this. Threading one through would suggest that arm is
    manifest-bounded when it is bounded by having no manifest at all.

    Note `MAX_SKILL_FILES` does **not** retire this. 500 entries render ~42 500 bytes
    against a 16 000 cap — only ~188 short paths fit — so the cap bounds storage and the
    add loop, and this bounds the render. Two limits, two jobs.
    """
    kept = list(files)
    omitted = 0
    while kept:
        rendered = _read_skill_payload(name, "", body_len, files=kept, files_omitted=omitted)
        if len(rendered) + _MIN_SPAN_CHARS <= _MAX_TOOL_OUTPUT:
            break
        kept.pop()
        omitted += 1
    return kept, omitted


def build_read_skill_tool(
    snapshot: BoundSet,
    *,
    db: Any = None,
    reads: list[SkillRead] | None = None,
) -> Tool:
    """R31.15 — load a bound skill's body or one of its files from the turn's snapshot.

    ``snapshot`` is what ``resolve_bound_set`` already validated this turn, and every
    lookup is against it. It must never re-query by name: the turn-time containment tap
    would then be decorative, and a name lookup against the `skills` table is an unscoped
    read primitive over every tenant's skills. The same rule is why the `path` arm
    resolves against the snapshot's own manifest and hands the resulting `SkillFile` to
    the facade, rather than passing an id the facade would have to look up.

    ``reads`` is AC-19's sink: each served read appends a `SkillRead`, and the turn engine
    folds them into `message.metadata`. Reads of a *file* are not recorded — R31.17 names
    the body hash, and a file read is already pinned by the body read that had to precede
    it to learn the path.
    """
    by_name = {s.name: s for s in snapshot.skills}
    # [R31.16] — "bodies fetched within a turn are cached for that turn only; edits take
    # effect on the next turn". Bodies get that for free by living in the snapshot; file
    # text does not, because it is fetched from MinIO on demand. Without this the rule is
    # broken twice: an edit landing mid-walk would make the *next* continuation read a
    # different document at an offset computed against the old one, so AC-33's spans stop
    # reassembling — and a long file would be re-fetched and re-parsed once per span.
    # The closure is per turn, so the cache's lifetime is exactly the rule's.
    file_text_cache: dict[uuid.UUID, str] = {}
    # `_fit_manifest`'s inputs — the skill's name, its body length, and the snapshot's
    # file list — are fixed for the whole turn, and the trim is O(files^2) because it
    # re-renders the payload once per popped entry. Without this it ran again for every
    # continuation span of the same body. Same lifetime and same reason as the text cache
    # above; both die with the closure.
    manifest_cache: dict[uuid.UUID, tuple[list[SkillFile], int]] = {}

    def _manifest_for(skill: Skill, files: Sequence[SkillFile]) -> tuple[list[SkillFile], int]:
        cached = manifest_cache.get(skill.id)
        if cached is None:
            cached = _fit_manifest(skill.name, len(skill.body), files)
            manifest_cache[skill.id] = cached
        return cached

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        name = str(args.get("name", ""))
        skill = by_name.get(name)
        if skill is None:
            # An unknown name is a tool error, never a turn failure (R31.15): the model
            # can misread the index, and one bad call must not cost the user the turn.
            available = ", ".join(sorted(by_name)) or "(none)"
            return _tool_error(f"Unknown skill {name!r}. Bound skills: {available}.")

        files = snapshot.files_for(skill.id)
        # AC-34 / R31.20 — fail closed before serving any bytes of this skill, body
        # included. The gate is checked here rather than at the index because a pending
        # scan is transient (seconds) and dropping the skill from every turn's index
        # while it settles would be noisier than an honest error at call time. A
        # quarantined file is not transient, and this is what the model is told about it.
        try:
            assert_readable(skill, list(files))
        except SkillUnreadable as exc:
            return _tool_error(
                f"Skill {name!r} is unavailable: its file {exc.path!r} has not passed "
                f"the malware scan. Do not guess its contents."
            )

        raw_offset = args.get("offset")
        offset = 0 if raw_offset is None else _opt_int(raw_offset)
        if offset is None:
            # Absent means "start at 0"; unparseable means the model sent something it
            # thinks is an offset and is wrong about. Coercing the second to 0 silently
            # restarts its continuation walk, and the two sibling error paths below and
            # above both say so out loud rather than guess.
            return _tool_error(f"offset must be an integer, got {raw_offset!r}.")

        raw_path = args.get("path")
        if raw_path is None:
            return _serve_body(skill, files, offset, reads, _manifest_for(skill, files))
        return await _serve_file(skill, files, str(raw_path), offset, db, file_text_cache)

    return Tool(
        name="read_skill",
        description=(
            "Load the full instructions of one skill listed in the skills index, by name. "
            "Call it when a skill's description matches the task you are working on. The "
            "result lists the skill's files; pass one of their paths back as `path` to read "
            "that file. Long results come back truncated with a truncated_at_offset; call "
            "again with that offset to read the next span."
        ),
        input_schema=_READ_SKILL_SCHEMA,
        invoke=_invoke,
    )


def _serve_body(
    skill: Skill,
    files: Sequence[SkillFile],
    offset: int,
    reads: list[SkillRead] | None,
    manifest: tuple[list[SkillFile], int],
) -> ToolResult:
    if offset < 0 or offset > len(skill.body):
        return _tool_error(f"offset {offset} is outside skill {skill.name!r} (0..{len(skill.body)}).")
    # Bounded before the span search, so that search always has a solution — see
    # `_fit_manifest`. Computed once per turn by the caller, not here: its inputs cannot
    # change between two spans of one body.
    shown, omitted = manifest
    span, next_offset = _fit_skill_body(skill.name, skill.body, offset, files=shown, files_omitted=omitted)
    if reads is not None:
        reads.append(
            SkillRead(
                skill_id=skill.id,
                name=skill.name,
                scope=skill.scope,
                version=skill.version,
                body_sha256=skill.body_sha256,
            )
        )
    return ToolResult(
        content=clip_tool_output(
            _read_skill_payload(skill.name, span, next_offset, files=shown, files_omitted=omitted)
        )
    )


async def _serve_file(
    skill: Skill,
    files: Sequence[SkillFile],
    path: str,
    offset: int,
    db: Any,
    text_cache: dict[uuid.UUID, str],
) -> ToolResult:
    match = next((f for f in files if f.path == path), None)
    if match is None:
        available = ", ".join(f.path for f in files) or "(none)"
        return _tool_error(f"Skill {skill.name!r} has no file {path!r}. Its files: {available}.")
    if match.kind is not SkillFileKind.REFERENCE:
        # R31.18: a script is staged for the interpreter and an asset is opaque bytes.
        # Rendering either into the prompt would be a decode of something that is not
        # text for the model to read, and for a script it would also be the wrong channel.
        return _tool_error(
            f"File {path!r} is a {match.kind.value}, not a reference document, and cannot be read as text."
        )
    text = text_cache.get(match.id)
    if text is None:
        # Imported lazily and called with the snapshot's own `SkillFile`: the facade takes
        # the row, never an id, so this cannot become a read primitive over other tenants'
        # files.
        from contexts.skills.interfaces.facade import SkillsFacade

        text = await SkillsFacade(db).read_skill_file_text(match)
        text_cache[match.id] = text
    if offset < 0 or offset > len(text):
        return _tool_error(f"offset {offset} is outside file {path!r} (0..{len(text)}).")
    span, next_offset = _fit_skill_body(skill.name, text, offset, path=path)
    return ToolResult(content=clip_tool_output(_read_skill_payload(skill.name, span, next_offset, path=path)))


def build_registry(
    db: Any,
    *,
    agent_id: uuid.UUID,
    skills: BoundSet,
    reads: list[SkillRead] | None = None,
    extra: list[Tool] | None = None,
    is_infra_error: InfraErrorCheck | None = None,
) -> ToolRegistry:
    """Assemble the per-turn tool table for ``agent_id``.

    ``skills`` is the turn's validated bound-set snapshot and is **required**, not
    defaulted: a caller that forgets it is then a type error rather than an agent that
    silently loses ``read_skill``. Empty is the ordinary case and costs nothing — the
    tool is only offered when something is bound.

    It is the whole ``BoundSet`` rather than its ``.skills``, because the bodies, the file
    manifest, and the scan statuses that gate them are three views of one snapshot and
    must not be able to drift apart between the tap and the tool.
    """
    tools: list[Tool] = [build_update_wakeup_tool(db, agent_id=agent_id)]
    if skills.skills:
        tools.append(build_read_skill_tool(skills, db=db, reads=reads))
    if extra:
        tools.extend(extra)
    return ToolRegistry(tools, is_infra_error=is_infra_error)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "BUILTIN_TOOL_NAMES",
    "InfraErrorCheck",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_cast_approval_vote_tool",
    "build_read_skill_tool",
    "build_registry",
    "build_update_wakeup_tool",
    "clip_tool_output",
    "schema_violations",
]
