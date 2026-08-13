"""The shipped agent packs: their content, their link to a course, their limits.

Four things are pinned here, and three of them exist because nothing else in the
system can check them:

- **AC-6, the cross-reference.** A pack declares the course it accompanies and the
  activity type keys its prompts are written against. The production loader does
  *not* resolve either, because doing so would create an
  ``agents/infrastructure -> activities/infrastructure`` edge. A test may read
  both catalogues, so the check lives here. Deleting or renaming an activity type
  without updating a pack fails this file.
- **AC-7, the import direction**, asserted statically for the same reason: no tool
  in CI covers it.
- **AC-9/10/11, the three prompt constraints.** They are content, not code, so a
  reviewer is the only other thing standing between a shipped prompt and a
  classroom. These assert over the *shipped* files rather than over fixtures --
  a constraint that holds only for a hand-built example is worth nothing.
- The loader's rejection rules, mirroring the course catalogue's own table.
"""

from __future__ import annotations

import ast
import copy
import json
import pathlib
from typing import Any

import pytest

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.infrastructure.examples.catalogue import available_courses, load_course
from contexts.agents.domain.models import AgentModelHint
from contexts.agents.infrastructure.examples.catalogue import (
    AgentPackDefinition,
    PackFileInvalid,
    available_packs,
    load_pack,
)

# The cross-reference tests below load a real course, and the course loader checks
# its validator_config against the process-global in-process registry. Registered
# at import *and* per test because another module clears the registry in a
# teardown: an import-only registration passes in isolation and fails in the full
# run (deviation D-6 of the platform-example dossier).
register_first_party_validators()


@pytest.fixture(autouse=True)
def _registered_validators() -> None:
    register_first_party_validators()


SHIPPED_PACKS: tuple[AgentPackDefinition, ...] = tuple(load_pack(k) for k in available_packs())
SHIPPED_AGENTS = [(pack, agent) for pack in SHIPPED_PACKS for agent in pack.agents]
AGENT_IDS = [f"{pack.pack_key}/{agent.key}" for pack, agent in SHIPPED_AGENTS]


class TestShippedPackContent:
    def test_both_packs_ship(self) -> None:
        assert available_packs() == ("creative-thinking-design", "creative-thinking-room")

    def test_the_room_pack_carries_the_three_classroom_roles(self) -> None:
        room = load_pack("creative-thinking-room")

        assert [a.key for a in room.agents] == [
            "ta-guidance-teacher",
            "sa-peer-catalyst",
            "aa-silent-analyst",
        ]
        assert [a.room_role for a in room.agents] == ["normal", "normal", "observer"]

    def test_the_design_agent_is_not_a_classroom_member(self) -> None:
        """Its own pack (Q-3), and marked as belonging in no class room.

        Advisory, since installing binds no room -- but the two together are what
        stop a reader installing "the pack" and finding a design agent
        interrupting a student discussion.
        """
        design = load_pack("creative-thinking-design")

        assert [a.key for a in design.agents] == ["da-lesson-designer"]
        assert design.agents[0].room_role is None

    def test_the_orchestration_is_carried_by_the_pack_not_left_to_the_installer(self) -> None:
        """TA leads, SA waits for a lull, AA observes on a longer one, DA is inert.

        Wake-up is per-agent config, not a room-wide behaviour, so this is the part
        of the example that cannot be reproduced by copying prompts alone.
        """
        by_key = {a.key: a for _, a in SHIPPED_AGENTS}

        ta = by_key["ta-guidance-teacher"].wakeup_config["triggers"]
        assert ta["every_n_messages"] == {"enabled": True, "n": 1}
        assert ta["silence_minutes"]["enabled"] is False

        sa = by_key["sa-peer-catalyst"].wakeup_config["triggers"]
        assert sa["every_n_messages"]["enabled"] is False
        assert sa["silence_minutes"]["enabled"] is True

        aa = by_key["aa-silent-analyst"].wakeup_config["triggers"]
        assert aa["every_n_messages"]["enabled"] is False
        assert aa["silence_minutes"]["enabled"] is True
        assert aa["silence_minutes"]["observer_autostop_rounds"] > 0

        da = by_key["da-lesson-designer"].wakeup_config["triggers"]
        assert da["every_n_messages"]["enabled"] is False
        assert da["silence_minutes"]["enabled"] is False

    def test_no_pack_ships_call_only(self) -> None:
        """`call_only` also widens A2A: `a2a_scope.evaluate` lets any a2a-enabled
        agent in the project call a call_only agent with no shared context. An
        inert config suppresses wake-ups identically and grants nothing, so shipped
        content must not leave that widening latent behind a flag."""
        for pack, agent in SHIPPED_AGENTS:
            triggers = agent.wakeup_config.get("triggers", {})
            assert "call_only" not in triggers, f"{pack.pack_key}/{agent.key}"

    @pytest.mark.parametrize(("pack", "agent"), SHIPPED_AGENTS, ids=AGENT_IDS)
    def test_every_agent_carries_a_usable_model_hint(self, pack: Any, agent: Any) -> None:
        assert isinstance(agent.preferred_model_hint, AgentModelHint)

    @pytest.mark.parametrize(("pack", "agent"), SHIPPED_AGENTS, ids=AGENT_IDS)
    def test_every_agent_carries_provenance_and_a_prompt(self, pack: Any, agent: Any) -> None:
        assert "Ke Pei-jung" in pack.source
        assert agent.system_prompt.strip()


class TestPacksResolveAgainstTheirCourse:
    """AC-6. The link the loader deliberately does not check (see the docstring)."""

    @pytest.mark.parametrize("pack", SHIPPED_PACKS, ids=[p.pack_key for p in SHIPPED_PACKS])
    def test_for_course_names_a_shipped_course(self, pack: AgentPackDefinition) -> None:
        assert pack.for_course in available_courses()

    @pytest.mark.parametrize(("pack", "agent"), SHIPPED_AGENTS, ids=AGENT_IDS)
    def test_every_bound_activity_type_exists_in_that_course(self, pack: Any, agent: Any) -> None:
        course_keys = {t.key for t in load_course(pack.for_course).activity_types}

        missing = [k for k in agent.binds_activity_types if k not in course_keys]

        assert not missing, f"{pack.pack_key}/{agent.key} binds unknown type(s) {missing}"


class TestPromptConstraints:
    """AC-9, AC-10, AC-11: the three things a shipped prompt must say.

    Asserted by substring rather than by meaning, which is the honest limit of a
    test here: it catches a constraint deleted or lost in an edit, not a prompt
    that states it and then undermines it three lines later. OQ-1 of the dossier
    records the dry-run that has to cover the rest.
    """

    @pytest.mark.parametrize(("pack", "agent"), SHIPPED_AGENTS, ids=AGENT_IDS)
    def test_no_agent_may_quote_a_participant_submission(self, pack: Any, agent: Any) -> None:
        """AC-9. Both shipped units set echo_includes_content false, so the room
        transcript deliberately withholds answer text; an agent reading the digest
        aloud reverses that for the whole class."""
        prompt = agent.system_prompt

        assert "引述" in prompt, f"{agent.key} states no quoting prohibition"
        assert "轉述" in prompt, f"{agent.key} states no paraphrasing prohibition"

    def test_the_analyst_disclaims_the_three_unscored_creativity_dimensions(self) -> None:
        """AC-10. filled_count operationalizes fluency alone; flexibility,
        originality and elaboration have no scorer and no delivered rubric."""
        aa = next(a for _, a in SHIPPED_AGENTS if a.key == "aa-silent-analyst")

        for dimension in ("變通力", "獨創力", "精進力"):
            assert dimension in aa.system_prompt
        assert "流暢力" in aa.system_prompt
        assert "filled_count" in aa.system_prompt

    @pytest.mark.parametrize(
        "agent_key",
        ["ta-guidance-teacher", "sa-peer-catalyst"],
    )
    def test_the_room_facing_agents_carry_the_unit_four_boundary(self, agent_key: str) -> None:
        """AC-11. Unit 4 collects negative-affect narratives from 13-year-olds."""
        agent = next(a for _, a in SHIPPED_AGENTS if a.key == agent_key)

        assert "不要追問" in agent.system_prompt
        assert "誘導" in agent.system_prompt
        assert "諮商" in agent.system_prompt

    def test_the_designer_requires_all_three_in_what_it_drafts(self) -> None:
        """DA drafts TA/SA prompts, so the constraints have to survive one hop."""
        da = next(a for _, a in SHIPPED_AGENTS if a.key == "da-lesson-designer")

        assert "三條限制" in da.system_prompt
        assert "缺一不算完成" in da.system_prompt

    def test_the_designer_states_it_cannot_write_back(self) -> None:
        """A design agent that appears to configure agents is the obvious
        misreading, and nothing in the platform stops a user believing it."""
        da = next(a for _, a in SHIPPED_AGENTS if a.key == "da-lesson-designer")

        assert "沒有辦法" in da.system_prompt
        assert "複製" in da.system_prompt


_BACKEND = pathlib.Path(__file__).resolve().parents[2]
_AGENTS = _BACKEND / "contexts" / "agents"


def _imported_modules(py: pathlib.Path) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `level > 0` is relative, which cannot leave the package.
            if node.level == 0 and node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def test_agents_does_not_import_the_activities_catalogue() -> None:
    """AC-7. The cross-reference check belongs in this file, not in the loader.

    ``contexts/agents`` legitimately reaches the activities context through its
    facade and through the context provider the turn engine imports; what it must
    not do is reach into that context's *infrastructure* to resolve a pack's
    course link.
    """
    offenders = [
        f"{py.relative_to(_BACKEND)}: {module}"
        for py in _AGENTS.rglob("*.py")
        for module in _imported_modules(py)
        if module.startswith("contexts.activities.infrastructure")
    ]

    assert not offenders, f"contexts/agents must not import activities infrastructure: {offenders}"


def _pack_document() -> dict[str, Any]:
    """A minimal pack that loads cleanly; each test breaks one thing in it."""
    return {
        "pack_key": "fixture-pack",
        "title": "Fixture pack",
        "source": "test fixture",
        "for_course": "fixture-course",
        "group_name": "測試代理",
        "agents": [
            {
                "key": "ta-fixture",
                "name": "TA 測試",
                "room_role": "normal",
                "preferred_model_hint": "claude",
                "system_prompt": "你是測試用的教師代理。",
                "temperature": 0.7,
                "wakeup_config": {"triggers": {"every_n_messages": {"enabled": True, "n": 1}}},
                "binds_activity_types": ["unit-one"],
            }
        ],
    }


def _write_pack(root: pathlib.Path, document: dict[str, Any], *, name: str | None = None) -> None:
    path = root / (name or f"{document['pack_key']}.json")
    path.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))


class TestLoaderAcceptsAWellFormedPack:
    def test_loads_a_pack_from_any_directory(self, tmp_path: pathlib.Path) -> None:
        _write_pack(tmp_path, _pack_document())

        pack = load_pack("fixture-pack", root=tmp_path)

        assert pack.pack_key == "fixture-pack"
        assert pack.for_course == "fixture-course"
        assert [a.key for a in pack.agents] == ["ta-fixture"]
        assert pack.agents[0].preferred_model_hint is AgentModelHint.CLAUDE
        assert pack.agents[0].temperature == 0.7

    def test_non_ascii_text_round_trips(self, tmp_path: pathlib.Path) -> None:
        """UTF-8 is pinned in the loader, not inherited from the host locale."""
        _write_pack(tmp_path, _pack_document())

        agent = load_pack("fixture-pack", root=tmp_path).agents[0]

        assert agent.name == "TA 測試"
        assert agent.system_prompt == "你是測試用的教師代理。"

    def test_a_null_room_role_and_null_temperature_are_legal(self, tmp_path: pathlib.Path) -> None:
        """Null means "no class room" and "provider default" -- both real states,
        distinct from a missing field, which is still an error."""

        def relax(doc: dict[str, Any]) -> None:
            doc["agents"][0]["room_role"] = None
            doc["agents"][0]["temperature"] = None

        document = _pack_document()
        relax(document)
        _write_pack(tmp_path, document)

        agent = load_pack("fixture-pack", root=tmp_path).agents[0]

        assert agent.room_role is None
        assert agent.temperature is None

    def test_available_packs_lists_the_json_files(self, tmp_path: pathlib.Path) -> None:
        _write_pack(tmp_path, _pack_document())
        _write_pack(tmp_path, {**_pack_document(), "pack_key": "another-pack"})
        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

        assert available_packs(root=tmp_path) == ("another-pack", "fixture-pack")


class TestLoaderRejectsAMalformedPack:
    def _load_broken(self, tmp_path: pathlib.Path, mutate: Any) -> str:
        document = _pack_document()
        mutate(document)
        _write_pack(tmp_path, document, name="fixture-pack.json")
        with pytest.raises(PackFileInvalid) as excinfo:
            load_pack("fixture-pack", root=tmp_path)
        message = str(excinfo.value)
        assert "fixture-pack.json" in message, message
        return message

    def test_a_missing_agent_field(self, tmp_path: pathlib.Path) -> None:
        message = self._load_broken(tmp_path, lambda doc: doc["agents"][0].pop("system_prompt"))
        assert "system_prompt" in message
        assert "agents[0]" in message

    def test_a_missing_room_role_is_not_defaulted(self, tmp_path: pathlib.Path) -> None:
        """Defaulting it would quietly decide whether an agent speaks in front of a
        class or watches in silence."""
        message = self._load_broken(tmp_path, lambda doc: doc["agents"][0].pop("room_role"))
        assert "room_role" in message

    def test_a_missing_top_level_field(self, tmp_path: pathlib.Path) -> None:
        message = self._load_broken(tmp_path, lambda doc: doc.pop("for_course"))
        assert "for_course" in message

    def test_an_unknown_field(self, tmp_path: pathlib.Path) -> None:
        """A typo'd field must not fall through: `room_roles` silently ignored
        would ship an agent whose intended role nothing records."""

        def typo(doc: dict[str, Any]) -> None:
            doc["agents"][0]["room_roles"] = "observer"

        message = self._load_broken(tmp_path, typo)
        assert "room_roles" in message

    def test_an_unknown_room_role(self, tmp_path: pathlib.Path) -> None:
        def bad_role(doc: dict[str, Any]) -> None:
            doc["agents"][0]["room_role"] = "facilitator"

        message = self._load_broken(tmp_path, bad_role)
        assert "room_role" in message
        assert "observer" in message

    def test_an_unknown_model_hint(self, tmp_path: pathlib.Path) -> None:
        def bad_hint(doc: dict[str, Any]) -> None:
            doc["agents"][0]["preferred_model_hint"] = "llama"

        message = self._load_broken(tmp_path, bad_hint)
        assert "preferred_model_hint" in message
        assert "claude" in message

    @pytest.mark.parametrize("temperature", [-0.1, 2.5, True, "0.7"])
    def test_a_temperature_outside_the_provider_range(self, tmp_path: pathlib.Path, temperature: Any) -> None:
        """`True` is in the list on purpose: bool subclasses int in Python, so it
        would otherwise pass as the temperature 1."""

        def bad_temp(doc: dict[str, Any]) -> None:
            doc["agents"][0]["temperature"] = temperature

        message = self._load_broken(tmp_path, bad_temp)
        assert "temperature" in message

    def test_a_wakeup_config_that_is_not_an_object(self, tmp_path: pathlib.Path) -> None:
        def bad_config(doc: dict[str, Any]) -> None:
            doc["agents"][0]["wakeup_config"] = "every message"

        message = self._load_broken(tmp_path, bad_config)
        assert "wakeup_config" in message

    def test_binds_that_is_not_a_list_of_strings(self, tmp_path: pathlib.Path) -> None:
        def bad_binds(doc: dict[str, Any]) -> None:
            doc["agents"][0]["binds_activity_types"] = "unit-one"

        message = self._load_broken(tmp_path, bad_binds)
        assert "binds_activity_types" in message

    def test_an_oversized_system_prompt(self, tmp_path: pathlib.Path) -> None:
        """Installing bypasses the request model, so the parser mirrors its bound
        rather than creating an agent the API would then refuse to edit."""

        def huge(doc: dict[str, Any]) -> None:
            doc["agents"][0]["system_prompt"] = "字" * 100_001

        message = self._load_broken(tmp_path, huge)
        assert "system_prompt" in message

    def test_a_duplicate_agent_key(self, tmp_path: pathlib.Path) -> None:
        def duplicate(doc: dict[str, Any]) -> None:
            doc["agents"].append(copy.deepcopy(doc["agents"][0]))

        message = self._load_broken(tmp_path, duplicate)
        assert "ta-fixture" in message
        assert "twice" in message

    def test_a_duplicate_agent_name(self, tmp_path: pathlib.Path) -> None:
        """Install is idempotent by name, so two agents sharing one would make the
        second permanently already-present."""

        def same_name(doc: dict[str, Any]) -> None:
            twin = copy.deepcopy(doc["agents"][0])
            twin["key"] = "sa-fixture"
            doc["agents"].append(twin)

        message = self._load_broken(tmp_path, same_name)
        assert "TA 測試" in message

    def test_an_empty_agent_list(self, tmp_path: pathlib.Path) -> None:
        message = self._load_broken(tmp_path, lambda doc: doc.__setitem__("agents", []))
        assert "agents" in message

    def test_a_pack_key_that_disagrees_with_the_filename(self, tmp_path: pathlib.Path) -> None:
        message = self._load_broken(tmp_path, lambda doc: doc.__setitem__("pack_key", "other-pack"))
        assert "pack_key" in message

    def test_invalid_json(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "fixture-pack.json").write_bytes(b'{"pack_key": ')
        with pytest.raises(PackFileInvalid, match="fixture-pack.json"):
            load_pack("fixture-pack", root=tmp_path)

    def test_a_file_that_is_not_utf8(self, tmp_path: pathlib.Path) -> None:
        document = json.dumps(_pack_document(), ensure_ascii=False)
        (tmp_path / "fixture-pack.json").write_bytes(document.encode("big5"))

        with pytest.raises(PackFileInvalid, match="not UTF-8"):
            load_pack("fixture-pack", root=tmp_path)

    def test_a_utf8_file_with_a_byte_order_mark(self, tmp_path: pathlib.Path) -> None:
        document = json.dumps(_pack_document(), ensure_ascii=False)
        (tmp_path / "fixture-pack.json").write_bytes(b"\xef\xbb\xbf" + document.encode("utf-8"))

        assert load_pack("fixture-pack", root=tmp_path).agents[0].name == "TA 測試"

    def test_an_absent_catalogue_directory(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "no-such-directory"

        assert available_packs(root=missing) == ()
        with pytest.raises(PackFileInvalid, match="available: none"):
            load_pack("fixture-pack", root=missing)

    @pytest.mark.parametrize(
        "pack_key",
        [
            "../secrets",
            "..",
            "a/b",
            "a\\b",
            "Pack",
            "with_underscore",
            "",
            "creative-thinking-room.json",
            # `$` would accept this; the guard is anchored with \Z so it does not.
            "creative-thinking-room\n",
            "nul\x00byte",
        ],
    )
    def test_a_key_that_could_escape_the_catalogue_directory(
        self, tmp_path: pathlib.Path, pack_key: str
    ) -> None:
        """The install route takes this from an HTTP path parameter."""
        with pytest.raises(PackFileInvalid, match="not a valid pack key"):
            load_pack(pack_key, root=tmp_path)
