"""Unit tests for prompt_studio config + template services (§29).

DB-free: services are built via ``__new__`` with fake repositories/facades
injected, so the resolution chain, key-ownership guard, scope-ownership guard
and template cap are exercised without Postgres. Integration coverage of the
repositories' SQL lives in the DB-backed suite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

import contexts.prompt_studio.application.config_service as config_mod
import contexts.prompt_studio.application.template_service as template_mod
from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.application.template_service import TemplateService
from contexts.prompt_studio.domain.errors import (
    AssistantConfigNotFound,
    PinnedKeyCapabilityMismatch,
    PinnedKeyNotOwned,
    TemplateLimitReached,
    TemplateNotFound,
    VersionMismatch,
)
from contexts.prompt_studio.domain.models import (
    TEMPLATES_PER_SCOPE_MAX,
    AssistantConfig,
    PromptScope,
    PromptTemplate,
    TemplateDraft,
)

_NOW = datetime(2026, 7, 5, tzinfo=UTC)


def _config(
    scope: PromptScope,
    *,
    enabled: bool,
    org_id=None,
    user_id=None,
    hide=False,
    persona="",
    name="",
    description="",
    config_id=None,
    version=1,
) -> AssistantConfig:
    return AssistantConfig(
        id=config_id or uuid.uuid4(),
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        persona_prompt=persona,
        name=name,
        description=description,
        system_prompt="sp",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=enabled,
        hide_platform_templates=hide,
        version=version,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _template(scope: PromptScope, *, org_id=None, user_id=None, name="t") -> PromptTemplate:
    return PromptTemplate(
        id=uuid.uuid4(),
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        name=name,
        description="",
        body="body",
        position=0,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


@dataclass
class _FakeConfigRepo:
    by_scope: dict[tuple, AssistantConfig]
    meta_calls: list = None  # type: ignore[assignment]
    full_calls: list = None  # type: ignore[assignment]
    platform_presets: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.meta_calls is None:
            self.meta_calls = []
        if self.full_calls is None:
            self.full_calls = []
        if self.platform_presets is None:
            self.platform_presets = []

    async def get_by_scope(self, scope, *, org_id=None, user_id=None):
        if scope is PromptScope.PLATFORM:
            return self.by_scope.get(("platform",))
        if scope is PromptScope.ORG:
            return self.by_scope.get(("org", org_id))
        return self.by_scope.get(("user", user_id))

    async def get_enabled_platform_preset(self):
        platform = self.by_scope.get(("platform",))
        if platform is not None and platform.enabled:
            return platform
        return next((p for p in self.platform_presets if p.enabled), None)

    async def list_files_meta(self, config_id):
        self.meta_calls.append(config_id)
        return []

    async def list_files(self, config_id):
        self.full_calls.append(config_id)
        return []

    # -- platform preset CRUD (test double for AssistantConfigRepository) ---

    async def list_platform(self):
        return list(self.platform_presets)

    async def get_by_id(self, config_id):
        return next((p for p in self.platform_presets if p.id == config_id), None)

    async def create(self, *, scope, org_id, user_id, values):
        preset = _config(
            scope,
            enabled=values["enabled"],
            org_id=org_id,
            user_id=user_id,
            persona=values.get("persona_prompt", ""),
            name=values.get("name", ""),
            description=values.get("description", ""),
        )
        self.platform_presets.append(preset)
        return preset

    async def update(self, config_id, *, expected_version, values):
        for i, p in enumerate(self.platform_presets):
            if p.id == config_id:
                if p.version != expected_version:
                    raise VersionMismatch(str(config_id))
                updated = _config(
                    p.scope,
                    enabled=values.get("enabled", p.enabled),
                    org_id=p.org_id,
                    user_id=p.user_id,
                    persona=values.get("persona_prompt", p.persona_prompt),
                    name=values.get("name", p.name),
                    description=values.get("description", p.description),
                    config_id=p.id,
                    version=p.version + 1,
                )
                self.platform_presets[i] = updated
                return updated
        raise AssistantConfigNotFound(str(config_id))

    async def delete(self, config_id):
        before = len(self.platform_presets)
        self.platform_presets = [p for p in self.platform_presets if p.id != config_id]
        return len(self.platform_presets) < before


@dataclass
class _FakeKey:
    owner_user_id: uuid.UUID


class _FakeKeys:
    def __init__(self, key, capability_ok=True):
        self._key = key
        self._cap = capability_ok

    async def get_key(self, key_id):
        return self._key

    async def validate_key_capability(self, key_id, capability):
        return self._cap


@dataclass
class _FakeProject:
    owner_org_id: uuid.UUID | None


class _FakeTenancy:
    def __init__(self, project):
        self._project = project

    async def get_project(self, project_id, *, include_deleted=False):
        return self._project


def _make_config_service(*, configs, keys=None, tenancy=None, monkeypatch, emitted=None) -> ConfigService:
    async def _record_emit(_db, event):
        if emitted is not None:
            emitted.append(event)

    monkeypatch.setattr(config_mod.audit, "emit", _record_emit)
    svc = ConfigService.__new__(ConfigService)
    svc._db = object()
    svc._configs = configs
    svc._keys = keys
    svc._tenancy = tenancy
    return svc


# --- resolution chain (AC-6 / R29.04) --------------------------------------


@pytest.mark.asyncio
async def test_personal_config_wins(monkeypatch) -> None:
    uid = uuid.uuid4()
    repo = _FakeConfigRepo(
        {
            ("user", uid): _config(PromptScope.USER, enabled=True, user_id=uid),
            ("platform",): _config(PromptScope.PLATFORM, enabled=True),
        }
    )
    svc = _make_config_service(
        configs=repo, tenancy=_FakeTenancy(_FakeProject(None)), monkeypatch=monkeypatch
    )
    resolved = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uid)
    assert resolved is not None
    assert resolved.scope is PromptScope.USER


@pytest.mark.asyncio
async def test_disabled_personal_falls_through_to_org(monkeypatch) -> None:
    uid, org = uuid.uuid4(), uuid.uuid4()
    repo = _FakeConfigRepo(
        {
            ("user", uid): _config(PromptScope.USER, enabled=False, user_id=uid),
            ("org", org): _config(PromptScope.ORG, enabled=True, org_id=org),
            ("platform",): _config(PromptScope.PLATFORM, enabled=True),
        }
    )
    svc = _make_config_service(configs=repo, tenancy=_FakeTenancy(_FakeProject(org)), monkeypatch=monkeypatch)
    resolved = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uid)
    assert resolved is not None
    assert resolved.scope is PromptScope.ORG


@pytest.mark.asyncio
async def test_personal_project_skips_org_uses_platform(monkeypatch) -> None:
    uid, org = uuid.uuid4(), uuid.uuid4()
    # An org config exists but the project is user-owned (owner_org_id None), so
    # the org config must NOT be considered — resolution jumps to platform.
    repo = _FakeConfigRepo(
        {
            ("org", org): _config(PromptScope.ORG, enabled=True, org_id=org),
            ("platform",): _config(PromptScope.PLATFORM, enabled=True),
        }
    )
    svc = _make_config_service(
        configs=repo, tenancy=_FakeTenancy(_FakeProject(None)), monkeypatch=monkeypatch
    )
    resolved = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uid)
    assert resolved is not None
    assert resolved.scope is PromptScope.PLATFORM


@pytest.mark.asyncio
async def test_disabled_platform_resolves_none(monkeypatch) -> None:
    repo = _FakeConfigRepo({("platform",): _config(PromptScope.PLATFORM, enabled=False)})
    svc = _make_config_service(
        configs=repo, tenancy=_FakeTenancy(_FakeProject(None)), monkeypatch=monkeypatch
    )
    resolved = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uuid.uuid4())
    assert resolved is None


# --- pinned-key guards (R29.05) --------------------------------------------


@pytest.mark.asyncio
async def test_put_config_rejects_unowned_key(monkeypatch) -> None:
    actor, other = uuid.uuid4(), uuid.uuid4()
    svc = _make_config_service(
        configs=_FakeConfigRepo({}),
        keys=_FakeKeys(_FakeKey(owner_user_id=other)),
        monkeypatch=monkeypatch,
    )
    with pytest.raises(PinnedKeyNotOwned):
        await svc.put_config(
            actor_user_id=actor,
            scope=PromptScope.USER,
            org_id=None,
            user_id=actor,
            persona_prompt="",
            system_prompt="x",
            key_id=uuid.uuid4(),
            model_id=None,
            daily_request_limit_per_user=50,
            enabled=True,
            hide_platform_templates=False,
            expected_version=None,
        )


@pytest.mark.asyncio
async def test_put_config_rejects_wrong_capability(monkeypatch) -> None:
    actor = uuid.uuid4()
    svc = _make_config_service(
        configs=_FakeConfigRepo({}),
        keys=_FakeKeys(_FakeKey(owner_user_id=actor), capability_ok=False),
        monkeypatch=monkeypatch,
    )
    with pytest.raises(PinnedKeyCapabilityMismatch):
        await svc.put_config(
            actor_user_id=actor,
            scope=PromptScope.USER,
            org_id=None,
            user_id=actor,
            persona_prompt="",
            system_prompt="x",
            key_id=uuid.uuid4(),
            model_id=None,
            daily_request_limit_per_user=50,
            enabled=True,
            hide_platform_templates=False,
            expected_version=None,
        )


@pytest.mark.asyncio
async def test_put_config_existing_requires_if_match(monkeypatch) -> None:
    actor = uuid.uuid4()
    existing = _config(PromptScope.USER, enabled=True, user_id=actor)
    svc = _make_config_service(
        configs=_FakeConfigRepo({("user", actor): existing}),
        keys=_FakeKeys(None),
        monkeypatch=monkeypatch,
    )
    with pytest.raises(VersionMismatch):
        await svc.put_config(
            actor_user_id=actor,
            scope=PromptScope.USER,
            org_id=None,
            user_id=actor,
            persona_prompt="",
            system_prompt="x",
            key_id=None,
            model_id=None,
            daily_request_limit_per_user=50,
            enabled=True,
            hide_platform_templates=False,
            expected_version=None,  # missing If-Match on an existing config
        )


@pytest.mark.asyncio
async def test_list_files_uses_metadata_only_projection(monkeypatch) -> None:
    # Regression: the API layer's response DTOs never serialize extracted_text
    # (up to 200 KB/config) -- ConfigService.list_files must route through the
    # metadata-only repository query, not the full-row one the worker uses.
    repo = _FakeConfigRepo({})
    svc = _make_config_service(configs=repo, monkeypatch=monkeypatch)
    config_id = uuid.uuid4()

    await svc.list_files(config_id)

    assert repo.meta_calls == [config_id]
    assert repo.full_calls == []


# --- platform preset CRUD (AC-5 / R29.16) -----------------------------------


@pytest.mark.asyncio
async def test_create_preset_lists_under_platform(monkeypatch) -> None:
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch)
    actor = uuid.uuid4()

    created = await svc.create_preset(
        actor_user_id=actor,
        name="General",
        description="desc",
        persona_prompt="persona",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=False,
    )

    assert created.scope is PromptScope.PLATFORM
    presets = await svc.list_platform_presets()
    assert [p.id for p in presets] == [created.id]


@pytest.mark.asyncio
async def test_enabling_a_preset_disables_the_previously_enabled_one(monkeypatch) -> None:
    # R29.16 / Gap-1 resolution: at most one platform preset is enabled at a
    # time -- enabling one auto-disables whichever was enabled before it, so
    # resolve_for_project's platform fallback is never ambiguous.
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch)
    actor = uuid.uuid4()

    first = await svc.create_preset(
        actor_user_id=actor,
        name="A",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=True,
    )
    second = await svc.create_preset(
        actor_user_id=actor,
        name="B",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=True,
    )

    presets = {p.id: p for p in await svc.list_platform_presets()}
    assert presets[first.id].enabled is False
    assert presets[second.id].enabled is True


@pytest.mark.asyncio
async def test_update_preset_to_enabled_disables_sibling(monkeypatch) -> None:
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch)
    actor = uuid.uuid4()

    first = await svc.create_preset(
        actor_user_id=actor,
        name="A",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=True,
    )
    second = await svc.create_preset(
        actor_user_id=actor,
        name="B",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=False,
    )

    updated_second = await svc.update_preset(
        preset_id=second.id,
        actor_user_id=actor,
        expected_version=second.version,
        name="B",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=True,
    )

    presets = {p.id: p for p in await svc.list_platform_presets()}
    assert presets[first.id].enabled is False
    assert updated_second.enabled is True


@pytest.mark.asyncio
async def test_delete_preset_removes_it(monkeypatch) -> None:
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch)
    actor = uuid.uuid4()
    preset = await svc.create_preset(
        actor_user_id=actor,
        name="A",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=False,
    )

    await svc.delete_preset(preset_id=preset.id, actor_user_id=actor)

    assert await svc.list_platform_presets() == []


@pytest.mark.asyncio
async def test_delete_preset_missing_raises_not_found(monkeypatch) -> None:
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch)
    with pytest.raises(AssistantConfigNotFound):
        await svc.delete_preset(preset_id=uuid.uuid4(), actor_user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_resolve_for_project_uses_enabled_platform_preset(monkeypatch) -> None:
    # AC-5 / AC-8: resolve_for_project's platform fallback must see a preset
    # created via create_preset, not just the legacy by_scope("platform") slot.
    repo = _FakeConfigRepo({})
    svc = _make_config_service(
        configs=repo, tenancy=_FakeTenancy(_FakeProject(None)), monkeypatch=monkeypatch
    )
    actor = uuid.uuid4()
    preset = await svc.create_preset(
        actor_user_id=actor,
        name="A",
        description="",
        persona_prompt="custom persona",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=True,
    )

    resolved = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uuid.uuid4())

    assert resolved is not None
    assert resolved.id == preset.id
    assert resolved.persona_prompt == "custom persona"


# --- audit logging (AC-12 / R29.13) -----------------------------------------


@pytest.mark.asyncio
async def test_put_config_emits_config_created(monkeypatch) -> None:
    actor = uuid.uuid4()
    emitted: list = []
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch, emitted=emitted)

    await svc.put_config(
        actor_user_id=actor,
        scope=PromptScope.USER,
        org_id=None,
        user_id=actor,
        persona_prompt="p",
        system_prompt="x",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=True,
        hide_platform_templates=False,
        expected_version=None,
    )

    assert [e.action for e in emitted] == ["prompt_studio.config_created"]
    assert emitted[0].actor_user_id == actor


@pytest.mark.asyncio
async def test_preset_crud_emits_the_matching_actions(monkeypatch) -> None:
    actor = uuid.uuid4()
    emitted: list = []
    svc = _make_config_service(configs=_FakeConfigRepo({}), monkeypatch=monkeypatch, emitted=emitted)

    preset = await svc.create_preset(
        actor_user_id=actor,
        name="A",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=False,
    )
    await svc.update_preset(
        preset_id=preset.id,
        actor_user_id=actor,
        expected_version=preset.version,
        name="A2",
        description="",
        persona_prompt="",
        system_prompt="",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=False,
    )
    await svc.delete_preset(preset_id=preset.id, actor_user_id=actor)

    assert [e.action for e in emitted] == [
        "prompt_studio.preset_created",
        "prompt_studio.preset_updated",
        "prompt_studio.preset_deleted",
    ]
    assert all(e.actor_user_id == actor for e in emitted)
    assert all(e.resource_type == "prompt_assistant_config" for e in emitted)


# --- template service ------------------------------------------------------


class _FakeTemplateRepo:
    def __init__(self, templates=None, count=0):
        self._by_id = {t.id: t for t in (templates or [])}
        self._by_scope: dict[tuple, list[PromptTemplate]] = {}
        for tpl in templates or []:
            key = (tpl.scope, tpl.org_id, tpl.user_id)
            self._by_scope.setdefault(key, []).append(tpl)
        self._count = count

    async def get(self, template_id):
        return self._by_id.get(template_id)

    async def count_for_scope(self, scope, *, org_id=None, user_id=None):
        return self._count

    async def list_for_scope(self, scope, *, org_id=None, user_id=None):
        return list(self._by_scope.get((scope, org_id, user_id), []))

    async def create(self, **kw):
        return _template(kw["scope"], org_id=kw["org_id"], user_id=kw["user_id"], name=kw["name"])

    async def update(self, template_id, *, expected_version, values):
        tpl = self._by_id[template_id]
        if tpl.version != expected_version:
            raise VersionMismatch(str(template_id))
        updated = PromptTemplate(
            id=tpl.id,
            scope=tpl.scope,
            org_id=tpl.org_id,
            user_id=tpl.user_id,
            name=values.get("name", tpl.name),
            description=values.get("description", tpl.description),
            body=values.get("body", tpl.body),
            position=values.get("position", tpl.position),
            version=tpl.version + 1,
            created_at=tpl.created_at,
            updated_at=tpl.updated_at,
        )
        self._by_id[template_id] = updated
        return updated


def _make_template_service(*, templates, configs=None, tenancy=None, monkeypatch) -> TemplateService:
    async def _noop_emit(*_a, **_k):
        return None

    monkeypatch.setattr(template_mod.audit, "emit", _noop_emit)
    svc = TemplateService.__new__(TemplateService)
    svc._db = object()
    svc._templates = templates
    svc._configs = configs
    svc._tenancy = tenancy
    return svc


@pytest.mark.asyncio
async def test_create_template_enforces_cap(monkeypatch) -> None:
    svc = _make_template_service(
        templates=_FakeTemplateRepo(count=TEMPLATES_PER_SCOPE_MAX), monkeypatch=monkeypatch
    )
    with pytest.raises(TemplateLimitReached):
        await svc.create_template(
            actor_user_id=uuid.uuid4(),
            scope=PromptScope.USER,
            org_id=None,
            user_id=uuid.uuid4(),
            name="n",
            description="",
            body="b",
        )


@pytest.mark.asyncio
async def test_update_template_rejects_cross_scope(monkeypatch) -> None:
    # A platform template edited via the personal (USER) scope path is 404.
    plat = _template(PromptScope.PLATFORM)
    svc = _make_template_service(templates=_FakeTemplateRepo([plat]), monkeypatch=monkeypatch)
    with pytest.raises(TemplateNotFound):
        await svc.delete_template(
            template_id=plat.id,
            scope=PromptScope.USER,
            org_id=None,
            user_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_update_template_rejects_other_users_template(monkeypatch) -> None:
    owner, attacker = uuid.uuid4(), uuid.uuid4()
    tpl = _template(PromptScope.USER, user_id=owner)
    svc = _make_template_service(templates=_FakeTemplateRepo([tpl]), monkeypatch=monkeypatch)
    with pytest.raises(TemplateNotFound):
        await svc.update_template(
            template_id=tpl.id,
            scope=PromptScope.USER,
            org_id=None,
            user_id=attacker,
            expected_version=1,
            draft=TemplateDraft(name="x"),
            actor_user_id=attacker,
        )


@pytest.mark.asyncio
async def test_update_template_noop_patch_still_checks_version(monkeypatch) -> None:
    # An empty-body PATCH (no fields set) must not bypass optimistic
    # concurrency -- a stale If-Match still has to 412.
    owner = uuid.uuid4()
    tpl = _template(PromptScope.USER, user_id=owner)
    svc = _make_template_service(templates=_FakeTemplateRepo([tpl]), monkeypatch=monkeypatch)
    with pytest.raises(VersionMismatch):
        await svc.update_template(
            template_id=tpl.id,
            scope=PromptScope.USER,
            org_id=None,
            user_id=owner,
            expected_version=tpl.version + 1,
            draft=TemplateDraft(),
            actor_user_id=owner,
        )


@pytest.mark.asyncio
async def test_update_template_noop_patch_with_correct_version_succeeds(monkeypatch) -> None:
    owner = uuid.uuid4()
    tpl = _template(PromptScope.USER, user_id=owner)
    svc = _make_template_service(templates=_FakeTemplateRepo([tpl]), monkeypatch=monkeypatch)
    result = await svc.update_template(
        template_id=tpl.id,
        scope=PromptScope.USER,
        org_id=None,
        user_id=owner,
        expected_version=tpl.version,
        draft=TemplateDraft(),
        actor_user_id=owner,
    )
    assert result.id == tpl.id
    assert result.version == tpl.version


@pytest.mark.asyncio
async def test_resolve_templates_hides_platform_when_org_opts_out(monkeypatch) -> None:
    uid, org = uuid.uuid4(), uuid.uuid4()
    plat = _template(PromptScope.PLATFORM)
    org_tpl = _template(PromptScope.ORG, org_id=org)
    user_tpl = _template(PromptScope.USER, user_id=uid)
    repo = _FakeTemplateRepo([plat, org_tpl, user_tpl])
    configs = _FakeConfigRepo({("org", org): _config(PromptScope.ORG, enabled=True, org_id=org, hide=True)})
    svc = _make_template_service(
        templates=repo, configs=configs, tenancy=_FakeTenancy(_FakeProject(org)), monkeypatch=monkeypatch
    )
    merged = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uid)
    scopes = {t.scope for t in merged}
    assert PromptScope.PLATFORM not in scopes
    assert PromptScope.ORG in scopes
    assert PromptScope.USER in scopes


@pytest.mark.asyncio
async def test_resolve_templates_personal_project_shows_platform_and_personal(monkeypatch) -> None:
    uid = uuid.uuid4()
    plat = _template(PromptScope.PLATFORM)
    user_tpl = _template(PromptScope.USER, user_id=uid)
    repo = _FakeTemplateRepo([plat, user_tpl])
    svc = _make_template_service(
        templates=repo,
        configs=_FakeConfigRepo({}),
        tenancy=_FakeTenancy(_FakeProject(None)),
        monkeypatch=monkeypatch,
    )
    merged = await svc.resolve_for_project(project_id=uuid.uuid4(), user_id=uid)
    scopes = {t.scope for t in merged}
    assert scopes == {PromptScope.PLATFORM, PromptScope.USER}
