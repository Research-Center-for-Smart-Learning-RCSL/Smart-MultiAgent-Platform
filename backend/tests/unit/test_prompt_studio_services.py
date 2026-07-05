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


def _config(scope: PromptScope, *, enabled: bool, org_id=None, user_id=None, hide=False) -> AssistantConfig:
    return AssistantConfig(
        id=uuid.uuid4(),
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        system_prompt="sp",
        key_id=None,
        model_id=None,
        daily_request_limit_per_user=50,
        enabled=enabled,
        hide_platform_templates=hide,
        version=1,
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

    async def get_by_scope(self, scope, *, org_id=None, user_id=None):
        if scope is PromptScope.PLATFORM:
            return self.by_scope.get(("platform",))
        if scope is PromptScope.ORG:
            return self.by_scope.get(("org", org_id))
        return self.by_scope.get(("user", user_id))


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


def _make_config_service(*, configs, keys=None, tenancy=None, monkeypatch) -> ConfigService:
    async def _noop_emit(*_a, **_k):
        return None

    monkeypatch.setattr(config_mod.audit, "emit", _noop_emit)
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
            system_prompt="x",
            key_id=None,
            model_id=None,
            daily_request_limit_per_user=50,
            enabled=True,
            hide_platform_templates=False,
            expected_version=None,  # missing If-Match on an existing config
        )


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
