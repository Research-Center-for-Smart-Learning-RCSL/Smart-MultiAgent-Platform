"""Skill CRUD, soft-delete, restore, and copy (§31).

Scope-ownership is proven here, so a caller holding `/api/orgs/{a}/skills` cannot mutate
org `b`'s skill by quoting its id — a mismatch reads as **404, never 403**, so existence
does not leak (`template_service.py:5-6`). Role-based AuthZ is at the API boundary; the
containment predicate that decides whether a skill may reach an *agent* is
`binding_service.resolve_bindable`, a separate control (§5).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.skills.application.binding_service import BindingService
from contexts.skills.domain.errors import (
    SkillIndexBudgetExceeded,
    SkillNameTaken,
    SkillNotFound,
    SkillRestoreConflict,
    SkillTextRejected,
    SkillVersionMismatch,
)
from contexts.skills.domain.models import (
    MAX_DESCRIPTION_CHARS,
    MAX_LIST_ITEMS,
    MAX_NAME_CHARS,
    MAX_TOOL_NAME_CHARS,
    SKILL_NAME_RE,
    Skill,
    SkillDraft,
    SkillScope,
    SkillSource,
)
from contexts.skills.domain.text_rules import assert_text_ok
from contexts.skills.infrastructure.repositories import (
    SkillBindingRepository,
    SkillRepository,
)
from shared_kernel import audit


def body_sha256(body: str) -> str:
    """Hash of the exact bytes that would execute.

    Recorded per read and per update (Q-15/Q-27) because bodies are mutable in place and
    there is no version tree: without it, the audit trail proves only that skill S was
    read at T and updated at T±1, never which bytes ran.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _assert_text_fields_ok(
    *,
    name: str | None = None,
    description: str | None = None,
    requires: tuple[str, ...] | None = None,
    allowed_tools: tuple[str, ...] | None = None,
) -> None:
    """Run the charset rule over every model-facing field of a write.

    **This is the gate the rule's coverage depends on** (`text_rules`). The API's Pydantic
    validators cover text arriving in a request body, but `copy`'s text comes from a stored
    row and the importer's from a stranger's file — neither crosses a request model, and
    the rule used to hold only where each writer's author remembered to call it.

    `None` means "not part of this write" and is skipped, so a patch touching one field
    does not fail on the fields it omits.

    Two fields are deliberately absent. `body` is multi-line markdown and the rule rejects
    newlines, so applying it here would reject every real skill (Q-1); it is capped at the
    API instead, never enters the index, and is escaped into a JSON tool result rather than
    into system-prompt text. `extra_frontmatter` is an untyped JSONB dict that no model
    ever sees — `render_index` emits only `name` and `description`, and `read_skill` does
    not carry it — so its rule is applied at the two places it can escape instead: the
    bundle parser on the way in and `render_skill_md` on the way out.
    """
    if name is not None:
        assert_text_ok("name", name, max_chars=MAX_NAME_CHARS)
        if not SKILL_NAME_RE.match(name):
            # The charset rule is not enough for `name` alone, because `name` is a
            # directory component under /workspace/skills/ (`turn_engine`) — so a
            # traversal segment here has a filesystem consequence the other fields do
            # not. Both real writers enforce the pattern already; it is restated at the
            # gate for the same reason the charset rule is, since "enforced wherever
            # someone remembered" is what this function exists to stop being true.
            raise SkillTextRejected("name", "must be lowercase alphanumeric with hyphens")
    if description is not None:
        assert_text_ok("description", description, max_chars=MAX_DESCRIPTION_CHARS)
    for field, values in (("requires", requires), ("allowed_tools", allowed_tools)):
        if values is None:
            continue
        if len(values) > MAX_LIST_ITEMS:
            # Count, not just per-item length: neither list reaches the index, so an
            # unbounded one is a load problem rather than an injection one — but it is
            # re-walked every turn, and only the API was capping it.
            raise SkillTextRejected(field, f"exceeds {MAX_LIST_ITEMS} entries")
        for value in values:
            assert_text_ok(field, value, max_chars=MAX_TOOL_NAME_CHARS)


class SkillService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._skills = SkillRepository(db)
        self._bindings = SkillBindingRepository(db)
        # One-way: binding_service never imports this module, so the dependency is
        # acyclic. Restore needs the bound-set rule, which binding_service owns (Q-30).
        self._binding_rules = BindingService(db)

    # -- reads ---------------------------------------------------------------

    async def list_for_scope(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        limit: int,
        offset: int,
        include_deleted: bool = False,
    ) -> tuple[list[Skill], int]:
        return await self._skills.list_for_scope(
            scope=scope,
            owner_id=owner_id,
            limit=limit,
            offset=offset,
            include_deleted=include_deleted,
        )

    async def get_owned(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        include_deleted: bool = False,
    ) -> Skill:
        return await self._assert_owned(skill_id, scope, owner_id=owner_id, include_deleted=include_deleted)

    # -- writes --------------------------------------------------------------

    async def create(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        requires: tuple[str, ...] = (),
        allowed_tools: tuple[str, ...] = (),
        extra_frontmatter: dict[str, Any] | None = None,
        source: SkillSource = SkillSource.AUTHORED,
        bundle_sha256: str | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        skill = await self._insert(
            scope=scope,
            owner_id=owner_id,
            name=name,
            description=description,
            body=body,
            requires=requires,
            allowed_tools=allowed_tools,
            extra_frontmatter=extra_frontmatter,
            source=source,
            bundle_sha256=bundle_sha256,
            actor_user_id=actor_user_id,
        )
        await self._emit("skill.created", skill, actor_user_id, actor_ip, request_id)
        return skill

    async def _insert(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        requires: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        extra_frontmatter: dict[str, Any] | None,
        source: SkillSource,
        bundle_sha256: str | None,
        actor_user_id: uuid.UUID,
    ) -> Skill:
        """Insert a row. Emits nothing — the caller owns the audit event.

        `create` and `copy` share this so a copy records exactly one event
        (`skill.copied`) rather than a `skill.created` immediately shadowed by it.
        """
        _assert_text_fields_ok(
            name=name,
            description=description,
            requires=requires,
            allowed_tools=allowed_tools,
        )

        if await self._skills.get_by_name(scope=scope, owner_id=owner_id, name=name) is not None:
            raise SkillNameTaken(name)

        return await self._skills.create(
            scope=scope,
            # Exactly one owner column is populated, chosen by the scope — the shape
            # ck_skill_scope enforces at the DB. Platform owns nothing.
            agent_id=owner_id if scope is SkillScope.AGENT else None,
            project_id=owner_id if scope is SkillScope.PROJECT else None,
            org_id=owner_id if scope is SkillScope.ORG else None,
            name=name,
            description=description,
            body=body,
            body_sha256=body_sha256(body),
            source=source,
            bundle_sha256=bundle_sha256,
            requires=requires,
            allowed_tools=allowed_tools,
            extra_frontmatter=extra_frontmatter or {},
            created_by=actor_user_id,
        )

    async def update(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        draft: SkillDraft,
        expected_version: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        """Patch a skill. `name` is not patchable — see `SkillDraft`."""
        current = await self._assert_owned(skill_id, scope, owner_id=owner_id)
        self._assert_version(current, expected_version)

        # After ownership, not before. The charset rule is about the caller's own input and
        # leaks nothing about the target, but this module's invariant is that a caller who
        # cannot prove ownership gets 404 and nothing else — which is a far easier property
        # to keep than a per-error argument about which ones happen to be safe to reveal.
        # Only what the draft carries: every field is optional on a patch, so validating an
        # absent one would fail on `None` rather than on any rule.
        _assert_text_fields_ok(
            description=draft.description,
            requires=draft.requires,
            allowed_tools=draft.allowed_tools,
        )

        values: dict[str, Any] = {}
        if draft.description is not None:
            values["description"] = draft.description
        if draft.body is not None:
            values["body"] = draft.body
            values["body_sha256"] = body_sha256(draft.body)
        if draft.requires is not None:
            values["requires"] = list(draft.requires)
        if draft.allowed_tools is not None:
            values["allowed_tools"] = list(draft.allowed_tools)
        if draft.extra_frontmatter is not None:
            values["extra_frontmatter"] = draft.extra_frontmatter

        if not values:
            return current

        if draft.description is not None and draft.description != current.description:
            # AC-6: lengthening a description is a write to the skill, but the budget it
            # can break belongs to every agent bound to it. Checked against the *would-be*
            # row before the write, so a rejected update leaves no trace.
            candidate = replace(current, description=draft.description)
            breaches = await self._binding_rules.agents_over_index_cap(candidate)
            if breaches:
                # The error carries one required/cap pair but may name many agents, so it
                # reports the *worst* breach: that is the agent whose numbers describe how
                # much the description has to shrink for the write to succeed everywhere.
                # Both figures come from the same agent — mixing them across agents, or
                # recomputing `required` from the candidate alone, yields a 422 whose own
                # arithmetic says it should have been a 200.
                worst = max(breaches, key=lambda b: b.overflow)
                raise SkillIndexBudgetExceeded(
                    required=worst.required,
                    cap=worst.cap,
                    agent_ids=tuple(b.agent_id for b in breaches),
                )

        updated = await self._skills.update(skill_id, values)
        if updated is None:  # lost a race with a concurrent delete
            raise SkillNotFound(skill_id)

        await self._emit(
            "skill.updated",
            updated,
            actor_user_id,
            actor_ip,
            request_id,
            # [R31.25]: the before/after hashes are what make an incident answerable at
            # all, since the body they describe is overwritten in place.
            extra={
                "body_sha256_before": current.body_sha256,
                "body_sha256_after": updated.body_sha256,
            },
        )
        return updated

    async def soft_delete(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        expected_version: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Soft-delete a skill and unbind it from every agent, in one transaction.

        The unbind is explicit and inside this transaction (the `clear_config_bindings`
        shape, F-18) rather than a FK CASCADE: a FK fires only on a physical row DELETE,
        never on the soft-delete UPDATE, so a CASCADE here would leave every binding live
        and pointing at a deleted skill. That is the exact defect 0054 exists to repair —
        copying its migration would have shipped the bug it repairs.

        Returns the agent ids that were unbound.
        """
        skill = await self._assert_owned(skill_id, scope, owner_id=owner_id)
        self._assert_version(skill, expected_version)

        unbound = await self._bindings.unbind_all_for_skill(skill_id)
        await self._skills.soft_delete(skill_id)

        await self._emit(
            "skill.deleted",
            skill,
            actor_user_id,
            actor_ip,
            request_id,
            extra={"unbound_agent_ids": [str(a) for a in unbound]},
        )
        return unbound

    async def restore(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        """Restore within the retention window, re-checking name uniqueness.

        Restores only the bindings *this* skill's delete cascaded
        (`cascade_deleted_at`), never one a user unbound on purpose beforehand
        (`deleted_at`) — a binding someone deliberately removed must not come back
        carrying an executable body (AC-37).
        """
        skill = await self._assert_owned(skill_id, scope, owner_id=owner_id, include_deleted=True)
        if skill.deleted_at is None:
            return skill

        # Two independent uniqueness rules must both still hold; a soft-deleted skill is
        # invisible to both, so either may have been taken while it was gone.
        # 1. Per scope holder ([R31.03]) — the partial unique indexes exclude deleted
        #    rows, so surface a collision as a 409 rather than let the index raise an
        #    opaque IntegrityError.
        clash = await self._skills.get_by_name(scope=scope, owner_id=owner_id, name=skill.name)
        if clash is not None:
            raise SkillRestoreConflict(skill.name)

        # 2. Across each agent's bound set (Q-30 / AC-39) — restoring re-attaches the
        #    cascaded bindings, and another skill may have taken the name inside one of
        #    those agents meanwhile. Naming the agents is what makes the 409 actionable.
        conflicting = await self._binding_rules.agents_conflicting_on_name(skill)
        if conflicting:
            raise SkillRestoreConflict(skill.name, agent_ids=conflicting)

        # 3. The index budget (AC-6). Restore is a *write that grows a bound set*, exactly
        #    like bind, so it belongs to the same rule: the delete shrank every bound
        #    agent's index, and the cap may have been lowered — or other skills bound —
        #    into the space this one is about to take back. Checked before the write,
        #    because there is deliberately no runtime truncation to fall back on: an
        #    unchecked restore would leave the agent over cap with nothing to detect it.
        breaches = await self._binding_rules.agents_over_index_cap_on_restore(skill)
        if breaches:
            worst = max(breaches, key=lambda b: b.overflow)
            raise SkillIndexBudgetExceeded(
                required=worst.required,
                cap=worst.cap,
                agent_ids=tuple(b.agent_id for b in breaches),
            )

        restored = await self._skills.restore(skill_id)
        if restored is None:
            raise SkillNotFound(skill_id)
        rebound = await self._bindings.restore_cascaded_for_skill(skill_id)

        await self._emit(
            "skill.restored",
            restored,
            actor_user_id,
            actor_ip,
            request_id,
            extra={"rebound_agent_ids": [str(a) for a in rebound]},
        )
        return restored

    async def copy(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        target_scope: SkillScope,
        target_owner_id: uuid.UUID | None,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        """Copy into another scope, fully detached (Q-6).

        No provenance link and no upstream sync: tracking one forces an upstream
        diff/merge UI. This is also how scope "changes" — scope is immutable (Q-5), so
        promoting is copying.
        """
        source_skill = await self._assert_owned(skill_id, scope, owner_id=owner_id)
        copied = await self._insert(
            scope=target_scope,
            owner_id=target_owner_id,
            name=name,
            description=source_skill.description,
            body=source_skill.body,
            requires=source_skill.requires,
            allowed_tools=source_skill.allowed_tools,
            extra_frontmatter=dict(source_skill.extra_frontmatter),
            # The copy is authored here, not imported: `bundle_sha256` is deliberately
            # not carried across, so the copy is never badged as diverged from a bundle
            # it has no relationship to.
            source=SkillSource.AUTHORED,
            bundle_sha256=None,
            actor_user_id=actor_user_id,
        )
        await self._emit(
            "skill.copied",
            copied,
            actor_user_id,
            actor_ip,
            request_id,
            extra={"source_skill_id": str(source_skill.id), "source_scope": source_skill.scope.value},
        )
        return copied

    async def cascade_owner_deleted(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Soft-delete an owner's skills and unbind them, inside the owner's transaction.

        Called when an agent / project / org soft-deletes. The FK CASCADE in 0056 cannot
        stand in for this: it fires only on a physical DELETE.
        """
        skill_ids = await self._skills.cascade_owner_deleted(scope=scope, owner_id=owner_id)
        for sid in skill_ids:
            await self._bindings.unbind_all_for_skill(sid)
        return skill_ids

    async def cascade_agent_deleted(self, agent_id: uuid.UUID) -> list[uuid.UUID]:
        """Unbind a soft-deleted agent's skills and delete its agent-scoped ones (AC-38)."""
        unbound = await self._bindings.unbind_all_for_agent(agent_id)
        await self.cascade_owner_deleted(scope=SkillScope.AGENT, owner_id=agent_id)
        return unbound

    # -- internals -----------------------------------------------------------

    async def _assert_owned(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        include_deleted: bool = False,
    ) -> Skill:
        """Prove the (skill, scope, owner) tuple, or 404.

        Capability ≠ target: `require(SKILL_ORG_MANAGE, scope_from_path(org_param))`
        proves the caller may manage skills in org X, never that *this* skill is in X.
        Every mismatch raises SkillNotFound — never 403, which would confirm the id
        exists to someone with no right to know.
        """
        skill = await self._skills.get(skill_id, include_deleted=include_deleted)
        if skill is None or skill.scope is not scope:
            raise SkillNotFound(skill_id)
        if skill.owner_id != owner_id:
            # Platform skills have owner_id None on both sides, so this compares equal
            # and correctly falls through — the check is not vacuous for the other three.
            raise SkillNotFound(skill_id)
        return skill

    @staticmethod
    def _assert_version(skill: Skill, expected: int | None) -> None:
        """If-Match optimistic locking. `None` means the client did not ask."""
        if expected is not None and expected != skill.version:
            raise SkillVersionMismatch(expected=expected, current=skill.version)

    async def _emit(
        self,
        action: str,
        skill: Skill,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "scope": skill.scope.value,
            "name": skill.name,
            "version": skill.version,
        }
        if extra:
            metadata.update(extra)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="skill",
                resource_id=skill.id,
                metadata=metadata,
                request_id=request_id,
            ),
        )


__all__ = ["SkillService", "body_sha256"]
