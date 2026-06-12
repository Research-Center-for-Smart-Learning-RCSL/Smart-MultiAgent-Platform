"""Transactional email bodies (R6.02 / R6.05 / R6.06 / R6.09–R6.11 / K.6).

Pure, dependency-free template functions: each returns a :class:`RenderedEmail`
(subject + plain-text body + minimal HTML body). The plain-text part is always
present (some clients/relays strip HTML); the HTML part is a thin, inline-styled
wrapper so the link is clickable without pulling in a templating engine.

Keys are en-only for v1 but the call sites pass already-localised display names,
so swapping in i18n later is a body-only change. Every body keeps the high-entropy
token in the URL *fragment* (`#token=`) — see ``auth_service._send_*`` for why.
"""

from __future__ import annotations

import html
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text_body: str
    html_body: str


def _wrap_html(
    heading: str,
    paragraphs: list[str],
    *,
    link: str | None = None,
    cta: str | None = None,
) -> str:
    """Minimal inline-styled HTML wrapper. ``link``/``cta`` render a button."""
    parts = [f"<h2 style='font:600 18px sans-serif;color:#111'>{html.escape(heading)}</h2>"]
    for para in paragraphs:
        parts.append(f"<p style='font:14px/1.5 sans-serif;color:#333'>{html.escape(para)}</p>")
    if link and cta:
        safe = html.escape(link, quote=True)
        parts.append(
            f"<p><a href='{safe}' "
            "style='display:inline-block;padding:10px 18px;background:#2563eb;"
            "color:#fff;text-decoration:none;border-radius:6px;font:600 14px sans-serif'>"
            f"{html.escape(cta)}</a></p>"
        )
        parts.append(
            "<p style='font:12px/1.4 sans-serif;color:#777'>"
            f"If the button doesn't work, copy this link:<br>{safe}</p>"
        )
    return f"<div style='max-width:480px;margin:0 auto'>{''.join(parts)}</div>"


def verify_email(link: str) -> RenderedEmail:
    return RenderedEmail(
        subject="Verify your email",
        text_body=(
            "Welcome to SMAP. Confirm your email address to activate your account:\n\n"
            f"{link}\n\nThis link is valid for 24 hours."
        ),
        html_body=_wrap_html(
            "Verify your email",
            [
                "Welcome to SMAP. Confirm your email address to activate your account.",
                "This link is valid for 24 hours.",
            ],
            link=link,
            cta="Verify email",
        ),
    )


def email_change_reverify(link: str) -> RenderedEmail:
    """Sent to the *new* address after a change-email request (R6.06)."""
    return RenderedEmail(
        subject="Confirm your new email",
        text_body=(
            "You requested to change your SMAP account email to this address. "
            f"Confirm to complete the change:\n\n{link}\n\n"
            "This link is valid for 24 hours. If you did not request this, ignore this message."
        ),
        html_body=_wrap_html(
            "Confirm your new email",
            [
                "You requested to change your SMAP account email to this address. "
                "Confirm to complete the change.",
                "This link is valid for 24 hours. If you did not request this, "
                "you can ignore this message.",
            ],
            link=link,
            cta="Confirm email",
        ),
    )


def password_reset(link: str) -> RenderedEmail:
    return RenderedEmail(
        subject="Reset your password",
        text_body=(
            "We received a request to reset your SMAP password. Use the link below "
            f"(valid 30 minutes):\n\n{link}\n\n"
            "If you did not request this, you can safely ignore this message."
        ),
        html_body=_wrap_html(
            "Reset your password",
            [
                "We received a request to reset your SMAP password. "
                "The link below is valid for 30 minutes.",
                "If you did not request this, you can safely ignore this message.",
            ],
            link=link,
            cta="Reset password",
        ),
    )


def already_registered(login_link: str) -> RenderedEmail:
    """Anti-enumeration notice (SEC-M4) — carries no token, grants nothing."""
    return RenderedEmail(
        subject="You already have an account",
        text_body=(
            "Someone tried to register an account with this email address, but one "
            f"already exists. If this was you, sign in at {login_link} or use the "
            "password-reset flow. If it wasn't, you can safely ignore this message."
        ),
        html_body=_wrap_html(
            "You already have an account",
            [
                "Someone tried to register an account with this email address, "
                "but one already exists.",
                "If this was you, sign in or use the password-reset flow. "
                "If it wasn't, you can safely ignore this message.",
            ],
            link=login_link,
            cta="Sign in",
        ),
    )


def invite(*, scope_label: str, scope_name: str, accept_link: str) -> RenderedEmail:
    """Org/project invite (R6.09). The link carries the plaintext invite token.

    The link lands on the SPA accept route; for an unregistered invitee the SPA
    routes them through sign-up first, then redeems the token (auto-enroll).
    """
    where = f"the {scope_label} “{scope_name}”" if scope_name else f"a {scope_label}"
    return RenderedEmail(
        subject=f"You've been invited to {where}",
        text_body=(
            f"You've been invited to join {where} on SMAP. Accept the invitation:\n\n"
            f"{accept_link}\n\n"
            "If you don't have an account yet, you'll be asked to create one first. "
            "This invitation expires in 7 days."
        ),
        html_body=_wrap_html(
            "You've been invited",
            [
                f"You've been invited to join {where} on SMAP.",
                "If you don't have an account yet, you'll be asked to create one "
                "first. This invitation expires in 7 days.",
            ],
            link=accept_link,
            cta="Accept invitation",
        ),
    )


__all__ = [
    "RenderedEmail",
    "already_registered",
    "email_change_reverify",
    "invite",
    "password_reset",
    "verify_email",
]
