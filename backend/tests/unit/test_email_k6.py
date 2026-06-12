"""Unit tests for K.6 — SMTP sender, factory selection, templates, captcha
config, and the invite email + accept-by-token path.

The compose-backed MailHog round-trip is the real exit criterion (CI `wiring`
job); these cover the pure/isolatable logic the round-trip rides on.
"""

from __future__ import annotations

import sys
import types
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from contexts.identity.application import factory
from contexts.identity.infrastructure import email as email_mod
from contexts.identity.infrastructure import email_templates as tmpl
from shared_kernel.auth import captcha
from shared_kernel.auth.clients import now


def _settings(*, env: str = "test", smtp_host: str = "", cors=()):
    return SimpleNamespace(
        app=SimpleNamespace(env=env),
        email=SimpleNamespace(
            smtp_host=smtp_host,
            smtp_port=587,
            smtp_from="SMAP <no-reply@test>",
            smtp_tls_mode="starttls",
            smtp_timeout_s=15.0,
        ),
        security=SimpleNamespace(cors_origins=list(cors)),
    )


# --------------------------------------------------------------------------- #
# build_mime + SmtpEmailSender                                                 #
# --------------------------------------------------------------------------- #


def test_build_mime_multipart_with_html() -> None:
    cid = uuid.uuid4()
    msg = email_mod.EmailMessage(
        to="u@example.com", subject="Hi", text_body="plain", html_body="<b>h</b>", correlation_id=cid
    )
    mime = email_mod.build_mime(msg, from_addr="from@x")
    assert mime["To"] == "u@example.com"
    assert mime["From"] == "from@x"
    assert mime["Subject"] == "Hi"
    assert mime["X-SMAP-Correlation-Id"] == str(cid)
    assert mime.is_multipart()
    subtypes = {p.get_content_subtype() for p in mime.iter_parts()}
    assert subtypes == {"plain", "html"}


def test_build_mime_text_only_when_no_html() -> None:
    mime = email_mod.build_mime(
        email_mod.EmailMessage(to="u@x", subject="S", text_body="body"), from_addr="f@x"
    )
    assert not mime.is_multipart()
    assert mime.get_content().strip() == "body"


@pytest.mark.parametrize(
    ("mode", "start_tls", "use_tls"),
    [("starttls", True, False), ("implicit", False, True), ("none", False, False)],
)
async def test_smtp_sender_tls_flag_mapping(monkeypatch, mode, start_tls, use_tls) -> None:
    captured: dict[str, object] = {}

    async def _fake_send(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)

    fake_mod = types.ModuleType("aiosmtplib")
    fake_mod.send = _fake_send  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiosmtplib", fake_mod)

    sender = email_mod.SmtpEmailSender(
        host="mail", port=2525, from_addr="f@x", tls_mode=mode, username="u", password="p"
    )
    await sender.send(email_mod.EmailMessage(to="t@x", subject="S", text_body="b"))
    assert captured["hostname"] == "mail"
    assert captured["port"] == 2525
    assert captured["start_tls"] is start_tls
    assert captured["use_tls"] is use_tls
    assert captured["username"] == "u"


# --------------------------------------------------------------------------- #
# Factory selection + startup warning                                          #
# --------------------------------------------------------------------------- #


def test_email_configured_reflects_host(monkeypatch) -> None:
    monkeypatch.setattr(factory, "get_settings", lambda: _settings(smtp_host=""))
    assert factory.email_configured() is False
    monkeypatch.setattr(factory, "get_settings", lambda: _settings(smtp_host="mail"))
    assert factory.email_configured() is True


def test_default_sender_is_logging_without_host(monkeypatch) -> None:
    monkeypatch.setattr(factory, "get_settings", lambda: _settings(smtp_host=""))
    assert isinstance(factory._default_email_sender(), email_mod.LoggingEmailSender)


def test_default_sender_is_smtp_with_host_and_vault_creds(monkeypatch) -> None:
    monkeypatch.setattr(factory, "get_settings", lambda: _settings(smtp_host="mail"))
    fake_vault = Mock()
    fake_vault.kv_get.return_value = {"username": "apikey", "password": "secret"}
    monkeypatch.setattr("shared_kernel.auth.clients.get_vault_client", lambda: fake_vault)
    sender = factory._default_email_sender()
    assert isinstance(sender, email_mod.SmtpEmailSender)
    assert sender._username == "apikey"
    assert sender._password == "secret"


def test_default_sender_smtp_without_vault_creds(monkeypatch) -> None:
    # Host set but Vault unreadable → SMTP without auth (relay/MailHog), not logging.
    monkeypatch.setattr(factory, "get_settings", lambda: _settings(smtp_host="mail"))

    def _boom():
        raise RuntimeError("vault down")

    monkeypatch.setattr("shared_kernel.auth.clients.get_vault_client", _boom)
    sender = factory._default_email_sender()
    assert isinstance(sender, email_mod.SmtpEmailSender)
    assert sender._username is None


async def test_lazy_email_sender_defers_construction() -> None:
    # The wrapper must NOT build the real sender until the first send — this is
    # what keeps login/refresh/list-invites off the Vault SMTP-secret read path.
    builds = 0
    captured = _CapturingSender()

    def _factory():
        nonlocal builds
        builds += 1
        return captured

    lazy = factory.LazyEmailSender(_factory)
    assert builds == 0  # constructing the wrapper builds nothing
    await lazy.send(email_mod.EmailMessage(to="t@x", subject="S", text_body="b"))
    await lazy.send(email_mod.EmailMessage(to="t@x", subject="S2", text_body="b2"))
    assert builds == 1  # built once, reused for the second send
    assert len(captured.sent) == 2


def test_warn_if_email_unconfigured_only_in_prod_without_host(monkeypatch) -> None:
    calls: list[str] = []
    fake_logger = SimpleNamespace(bind=lambda **_: SimpleNamespace(warning=lambda m: calls.append(m)))
    monkeypatch.setattr(factory, "logger", fake_logger)

    monkeypatch.setattr(factory, "get_settings", lambda: _settings(env="prod", smtp_host=""))
    factory.warn_if_email_unconfigured()
    assert len(calls) == 1

    monkeypatch.setattr(factory, "get_settings", lambda: _settings(env="prod", smtp_host="mail"))
    factory.warn_if_email_unconfigured()
    assert len(calls) == 1  # configured → no new warning

    monkeypatch.setattr(factory, "get_settings", lambda: _settings(env="dev", smtp_host=""))
    factory.warn_if_email_unconfigured()
    assert len(calls) == 1  # non-prod → no warning


# --------------------------------------------------------------------------- #
# Templates                                                                    #
# --------------------------------------------------------------------------- #


def test_templates_carry_link_in_text_and_html() -> None:
    link = "https://app.example/verify-email#token=ABC"
    for rendered in (
        tmpl.verify_email(link),
        tmpl.email_change_reverify(link),
        tmpl.password_reset(link),
        tmpl.already_registered("https://app.example/login"),
        tmpl.invite(scope_label="org", scope_name="Acme", accept_link=link),
    ):
        assert rendered.subject
        assert rendered.text_body
        assert rendered.html_body.startswith("<div")
    assert link in tmpl.verify_email(link).text_body
    assert link in tmpl.verify_email(link).html_body
    inv = tmpl.invite(scope_label="org", scope_name="Acme", accept_link=link)
    assert "Acme" in inv.text_body
    assert "org" in inv.text_body


# --------------------------------------------------------------------------- #
# captcha.public_config                                                        #
# --------------------------------------------------------------------------- #


def test_public_config_off_in_test_env(monkeypatch) -> None:
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(app=SimpleNamespace(env="test")))
    cfg = captcha.public_config()
    assert (cfg.mode, cfg.provider, cfg.sitekey) == ("off", "off", "")


def test_public_config_reads_vault_without_secret(monkeypatch) -> None:
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(app=SimpleNamespace(env="prod")))
    fake_vault = Mock()
    fake_vault.kv_get.return_value = {
        "provider": "hcaptcha",
        "sitekey": "sk-pub",
        "secret": "sk-SECRET",
        "mode": "on",
    }
    monkeypatch.setattr(captcha, "get_vault_client", lambda: fake_vault)
    cfg = captcha.public_config()
    assert cfg.provider == "hcaptcha"
    assert cfg.sitekey == "sk-pub"
    assert cfg.mode == "on"
    # The secret must never appear on the public object.
    assert "sk-SECRET" not in repr(cfg)


def test_public_config_mode_off_forces_provider_off(monkeypatch) -> None:
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(app=SimpleNamespace(env="prod")))
    fake_vault = Mock()
    fake_vault.kv_get.return_value = {"provider": "turnstile", "sitekey": "x", "mode": "off"}
    monkeypatch.setattr(captcha, "get_vault_client", lambda: fake_vault)
    cfg = captcha.public_config()
    assert cfg.mode == "off"
    assert cfg.provider == "off"


def test_public_config_fails_open_when_vault_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(app=SimpleNamespace(env="prod")))

    def _boom():
        raise RuntimeError("vault down")

    monkeypatch.setattr(captcha, "get_vault_client", _boom)
    cfg = captcha.public_config()
    assert cfg.provider == "off"


# --------------------------------------------------------------------------- #
# InviteService — invite email + accept-by-token                              #
# --------------------------------------------------------------------------- #


def _invite_service(sender):
    from contexts.tenancy.application.invite_service import InviteService

    db = Mock()
    return InviteService(db, email_sender=sender, public_origin="https://app.example")


class _CapturingSender:
    def __init__(self) -> None:
        self.sent: list[email_mod.EmailMessage] = []

    async def send(self, msg: email_mod.EmailMessage) -> None:
        self.sent.append(msg)


async def test_email_invite_carries_token_link(monkeypatch) -> None:
    from contexts.tenancy.domain.models import InviteScope

    sender = _CapturingSender()
    svc = _invite_service(sender)
    # _scope_name issues one db.execute(...).first()
    result = Mock()
    result.first.return_value = SimpleNamespace(name="Acme")
    svc._db.execute = AsyncMock(return_value=result)

    await svc._email_invite("invitee@x.com", "TOK123", InviteScope.ORG, uuid.uuid4())

    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg.to == "invitee@x.com"
    assert "https://app.example/invites/accept#token=TOK123" in msg.text_body
    assert "Acme" in msg.text_body


async def test_accept_by_token_finalizes_membership(monkeypatch) -> None:
    from contexts.tenancy.application import invite_service as isvc
    from contexts.tenancy.domain.models import InviteScope, InviteState

    monkeypatch.setattr(isvc.audit, "emit", AsyncMock())
    svc = _invite_service(_CapturingSender())

    invite_id = uuid.uuid4()
    pending = SimpleNamespace(
        id=invite_id,
        state=InviteState.PENDING,
        expires_at=now() + timedelta(days=1),
        scope_type=InviteScope.ORG,
        scope_id=uuid.uuid4(),
        role="member",
    )
    accepted = SimpleNamespace(**{**pending.__dict__, "state": InviteState.ACCEPTED})

    svc._invites = AsyncMock()
    svc._invites.get_by_token.return_value = pending
    svc._invites.transition.return_value = accepted
    svc._org_members = AsyncMock()

    caller = uuid.uuid4()
    out = await svc.accept_by_token(token="TOK", caller_user_id=caller, actor_ip=None)

    svc._invites.get_by_token.assert_awaited_once_with("TOK")
    svc._org_members.add.assert_awaited_once()
    assert out.state is InviteState.ACCEPTED


async def test_accept_by_token_rejects_unknown_token(monkeypatch) -> None:
    from contexts.tenancy.application import invite_service as isvc
    from contexts.tenancy.domain.errors import InviteNotFound

    monkeypatch.setattr(isvc.audit, "emit", AsyncMock())
    svc = _invite_service(_CapturingSender())
    svc._invites = AsyncMock()
    svc._invites.get_by_token.return_value = None

    with pytest.raises(InviteNotFound):
        await svc.accept_by_token(token="bad", caller_user_id=uuid.uuid4(), actor_ip=None)
