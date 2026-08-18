"""Agent-pack catalogue: the shape of a pack and how one is read off disk.

A parser, not a writer, and the deliberate mirror of
``contexts/activities/infrastructure/examples/catalogue.py`` -- same strictness,
same error shape, same traversal guard. The two are separate because the artifacts
differ: a course installs platform-wide as activity types, a pack copies into one
project as agents.

**This module must not import the course catalogue.** A pack names the course it
accompanies (``for_course``) and the activity type keys each agent is written for
(``binds_activity_types``), but resolving those against the shipped courses would
create an ``agents/infrastructure -> activities/infrastructure`` edge. The
cross-check lives in ``tests/unit/test_agent_example_packs.py`` instead, which may
read both because it is a test.

Pack files are JSON under ``packs/`` for the same reason courses are: the people
who author the content are educators, not engineers, and a prompt edit should not
require Python. Every field is required, including ``room_role``. Defaulting it
would quietly decide whether an agent speaks in front of a class or watches in
silence, which is not a default's decision to make.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from contexts.agents.domain.models import AgentModelHint

PACKS_DIRNAME = "packs"

# A pack key is also a filename component. Anchored \A..\Z rather than ^..$ for the
# reason the course loader records: `$` also matches before a trailing newline,
# which would let a key through carrying a character with no business in a path.
_PACK_KEY_RE = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")

_PACK_FIELDS = frozenset({"pack_key", "title", "source", "for_course", "group_name", "agents"})
_AGENT_FIELDS = frozenset(
    {
        "key",
        "name",
        "room_role",
        "preferred_model_hint",
        "system_prompt",
        "temperature",
        "wakeup_config",
        "binds_activity_types",
        "may_control_activities",
    }
)

# `normal` and `observer` mirror ChatroomAgentRole; `null` marks an agent that is
# not meant to sit in a class room at all (the design agent). Advisory in both
# cases: installing a pack binds no room, so this is documentation for the UI and
# the docs, never an enforced constraint.
_ROOM_ROLES = frozenset({"normal", "observer"})

# Mirrors `_MAX_SYSTEM_PROMPT` in `app/api/v1/agents.py`. Installing bypasses the
# request model, so without this a pack could create an agent the API itself would
# refuse to accept on the next edit.
_MAX_SYSTEM_PROMPT = 100_000

# Packs deliberately do not carry `triggers.call_only`. It reads as "explicit
# invocation only", and it does suppress autonomous wake-ups, but it is also an
# A2A authorization widener: `a2a_scope.evaluate` lets any a2a-enabled agent in
# the project call a call_only agent without a shared context. Shipping it would
# leave that widening latent behind a flag nobody would re-read when enabling
# a2a later. An agent meant to speak only when named disables both triggers
# instead, which `WakeupConfig.is_inert` treats identically for wake-ups and
# which grants nothing.


class PackFileInvalid(ValueError):
    """A pack file does not describe a usable pack.

    Raised with the file name and the offending key, because whoever sees this is
    editing JSON by hand and needs to know where to look.
    """


@dataclass(frozen=True, slots=True)
class PackAgent:
    """One installable agent, mirroring the fields ``AgentDraft`` needs."""

    key: str
    name: str
    # None means "not for a class room" -- see _ROOM_ROLES.
    room_role: str | None
    preferred_model_hint: AgentModelHint
    system_prompt: str
    temperature: float | None
    wakeup_config: dict[str, Any]
    binds_activity_types: tuple[str, ...]
    # Whether this agent's prompt is written to hold delegated activity control
    # ([R30.37], [R30.35]). **Advisory metadata, never an applied grant**: installing
    # a pack creates no chatroom and no room binding, so there is nothing to grant
    # it on. The room creator grants it per room, per agent, after binding.
    # Required rather than defaulted, for the reason `room_role` is: whether an
    # agent is written to start and end rounds in front of a class is not a
    # default's decision to make.
    may_control_activities: bool


@dataclass(frozen=True, slots=True)
class AgentPackDefinition:
    """One shipped pack: its provenance, the course it accompanies, its agents."""

    pack_key: str
    title: str
    source: str
    for_course: str
    group_name: str
    agents: tuple[PackAgent, ...]


def _fail(source: str, where: str, problem: str) -> PackFileInvalid:
    return PackFileInvalid(f"{source}: {where}: {problem}")


def _require_fields(source: str, where: str, data: dict[str, Any], allowed: frozenset[str]) -> None:
    missing = sorted(allowed - data.keys())
    if missing:
        raise _fail(source, where, f"missing required field(s) {', '.join(missing)}")
    unknown = sorted(data.keys() - allowed)
    if unknown:
        raise _fail(source, where, f"unknown field(s) {', '.join(unknown)}")


def _require_str(source: str, where: str, data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"{where}.{key}", "must be a non-empty string")
    return value


def _require_bool(source: str, where: str, data: dict[str, Any], key: str) -> bool:
    """Mirrors the course catalogue's helper of the same name.

    Strictly ``bool``: JSON's ``0``/``1`` and ``"false"`` are all truthy-or-falsy
    in Python and none of them is what the field means.
    """
    value = data[key]
    if not isinstance(value, bool):
        raise _fail(source, f"{where}.{key}", "must be true or false")
    return value


def _parse_room_role(source: str, where: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _ROOM_ROLES:
        allowed = ", ".join(sorted(_ROOM_ROLES))
        raise _fail(source, f"{where}.room_role", f"must be null or one of {allowed}")
    return value


def _parse_model_hint(source: str, where: str, value: Any) -> AgentModelHint:
    try:
        return AgentModelHint(value)
    except ValueError as exc:
        allowed = ", ".join(h.value for h in AgentModelHint)
        raise _fail(source, f"{where}.preferred_model_hint", f"{value!r} is not one of {allowed}") from exc


def _parse_temperature(source: str, where: str, value: Any) -> float | None:
    """``null`` leaves the provider default in place; otherwise 0.0-2.0.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in Python, so
    ``True`` would otherwise pass as the temperature 1.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int | float)):
        raise _fail(source, f"{where}.temperature", "must be null or a number")
    if not 0.0 <= float(value) <= 2.0:
        raise _fail(source, f"{where}.temperature", "must be between 0.0 and 2.0")
    return float(value)


def _parse_system_prompt(source: str, where: str, data: dict[str, Any]) -> str:
    prompt = _require_str(source, where, data, "system_prompt")
    if len(prompt) > _MAX_SYSTEM_PROMPT:
        raise _fail(
            source,
            f"{where}.system_prompt",
            f"is {len(prompt)} characters, above the {_MAX_SYSTEM_PROMPT} the API accepts",
        )
    return prompt


def _parse_wakeup_config(source: str, where: str, value: Any) -> dict[str, Any]:
    """Shape-checked only; the orchestration context owns the semantics.

    An empty object is legal and meaningful -- it is how the design agent declares
    itself inert, reachable by mention alone -- so emptiness is not an error here.
    """
    if not isinstance(value, dict):
        raise _fail(source, f"{where}.wakeup_config", "must be an object")
    triggers = value.get("triggers", {})
    if not isinstance(triggers, dict):
        raise _fail(source, f"{where}.wakeup_config.triggers", "must be an object")
    return value


def _parse_binds(source: str, where: str, value: Any) -> tuple[str, ...]:
    """The activity type keys this agent's prompt is written against.

    Not resolved here -- see the module docstring. An empty list is legal: an agent
    may be written for a course without being written against a specific worksheet.
    """
    if not isinstance(value, list) or not all(isinstance(k, str) and k.strip() for k in value):
        raise _fail(source, f"{where}.binds_activity_types", "must be an array of non-empty strings")
    return tuple(value)


def _parse_agent(source: str, index: int, data: Any) -> PackAgent:
    where = f"agents[{index}]"
    if not isinstance(data, dict):
        raise _fail(source, where, "must be an object")
    _require_fields(source, where, data, _AGENT_FIELDS)

    key = _require_str(source, where, data, "key")
    where = f"agents[{index}] '{key}'"

    return PackAgent(
        key=key,
        name=_require_str(source, where, data, "name"),
        room_role=_parse_room_role(source, where, data["room_role"]),
        preferred_model_hint=_parse_model_hint(source, where, data["preferred_model_hint"]),
        system_prompt=_parse_system_prompt(source, where, data),
        temperature=_parse_temperature(source, where, data["temperature"]),
        wakeup_config=_parse_wakeup_config(source, where, data["wakeup_config"]),
        binds_activity_types=_parse_binds(source, where, data["binds_activity_types"]),
        may_control_activities=_require_bool(source, where, data, "may_control_activities"),
    )


def parse_pack(data: Any, *, source: str) -> AgentPackDefinition:
    """Validate one decoded pack document. ``source`` names it in every error."""
    if not isinstance(data, dict):
        raise _fail(source, "pack", "must be a JSON object")
    _require_fields(source, "pack", data, _PACK_FIELDS)

    raw_agents = data["agents"]
    if not isinstance(raw_agents, list) or not raw_agents:
        raise _fail(source, "agents", "must be a non-empty array")

    agents = tuple(_parse_agent(source, i, a) for i, a in enumerate(raw_agents))

    seen: set[str] = set()
    for agent in agents:
        if agent.key in seen:
            raise _fail(source, f"agents '{agent.key}'", "is declared twice")
        seen.add(agent.key)

    # Names, not only keys, because install is idempotent by name within a project:
    # two agents sharing a name would make the second permanently already-present.
    names: set[str] = set()
    for agent in agents:
        if agent.name in names:
            raise _fail(source, f"agents '{agent.key}'", f"reuses the name {agent.name!r}")
        names.add(agent.name)

    return AgentPackDefinition(
        pack_key=_require_str(source, "pack", data, "pack_key"),
        title=_require_str(source, "pack", data, "title"),
        source=_require_str(source, "pack", data, "source"),
        for_course=_require_str(source, "pack", data, "for_course"),
        group_name=_require_str(source, "pack", data, "group_name"),
        agents=agents,
    )


def packs_root() -> Traversable:
    """The shipped pack directory, resolved as package data."""
    return files(__package__).joinpath(PACKS_DIRNAME)


def available_packs(*, root: Traversable | Path | None = None) -> tuple[str, ...]:
    """Pack keys the catalogue ships, sorted.

    An absent or unreadable directory reads as an empty catalogue rather than
    raising: that is the shape of the packaging failure this guards against (a
    wheel built without the pack files), and :func:`load_pack` reports it as
    "available: none" -- a diagnosis rather than a traceback.
    """
    base = root if root is not None else packs_root()
    try:
        entries = list(base.iterdir())
    except OSError:
        return ()
    return tuple(sorted(e.name[: -len(".json")] for e in entries if e.name.endswith(".json")))


def load_pack(pack_key: str, *, root: Traversable | Path | None = None) -> AgentPackDefinition:
    """Read and validate one pack file.

    ``pack_key`` reaches here from an HTTP path parameter, so ``_PACK_KEY_RE``
    bounds a network-reachable path rather than a CLI argument: anything that is
    not lowercase words joined by hyphens is refused before the filesystem is
    touched.
    """
    if not _PACK_KEY_RE.match(pack_key):
        raise PackFileInvalid(f"{pack_key!r} is not a valid pack key (lowercase words joined by hyphens)")

    base = root if root is not None else packs_root()
    filename = f"{pack_key}.json"
    pack_file = base.joinpath(filename)
    if not pack_file.is_file():
        known = ", ".join(available_packs(root=root)) or "none"
        raise PackFileInvalid(f"{filename}: no such pack in the catalogue (available: {known})")

    # Explicit UTF-8 for the same two reasons the course loader gives: pack text is
    # Chinese and the platform default would mojibake it on a Windows host, and
    # `-sig` because a Windows editor commonly writes a BOM that plain utf-8 keeps
    # as a leading character which then fails as JSON.
    try:
        raw = pack_file.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _fail(filename, "encoding", "is not UTF-8; re-save the file as UTF-8") from exc
    except OSError as exc:
        raise _fail(filename, "file", f"could not be read: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _fail(filename, f"line {exc.lineno}", f"is not valid JSON: {exc.msg}") from exc

    pack = parse_pack(data, source=filename)
    if pack.pack_key != pack_key:
        raise _fail(filename, "pack.pack_key", f"is {pack.pack_key!r} but the file is named {filename!r}")
    return pack


__all__ = [
    "PACKS_DIRNAME",
    "AgentPackDefinition",
    "PackAgent",
    "PackFileInvalid",
    "available_packs",
    "load_pack",
    "packs_root",
    "parse_pack",
]
