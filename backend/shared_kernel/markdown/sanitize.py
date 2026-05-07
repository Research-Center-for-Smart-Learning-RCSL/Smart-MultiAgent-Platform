"""Bleach-backed allowlist sanitiser + markdown → HTML pipeline (R13.14).

Scope:
  - Strip `<script>`, event handlers, `javascript:` / `data:text/html` URIs,
    `<object>`, `<embed>`, `<iframe>`, `<form>`, `<meta http-equiv="refresh">`.
  - Strip CSS payloads: `url(...)`, `@import`, `expression(...)` — these are
    not caught by bleach's attribute whitelist because they live in `style`.
  - Render markdown with `markdown-it-py` (CommonMark + tables) and run the
    output through the allowlist.

The client does a second pass via DOMPurify before displaying; server-side
sanitisation here protects export artefacts (JSON manifest includes the
sanitised HTML per R13.14) and ensures anything persisted is safe.
"""

from __future__ import annotations

import re

import bleach
from markdown_it import MarkdownIt

from shared_kernel.observability.metrics import MESSAGE_SANITIZE_REJECTIONS

# bleach's default `tags` allow-list is too small for technical docs; we
# extend with commonly-used block & inline elements but deliberately omit
# the dangerous-in-context ones listed in R13.14.
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)

_ALLOWED_ATTRS: dict[str, list[str]] = {
    "*": ["class", "id", "title"],
    "a": ["href", "rel", "target"],
    "img": ["src", "alt", "width", "height"],
    "code": ["class"],  # language-… for highlight.js
    "pre": ["class"],
    "span": ["class"],
    "div": ["class"],
}

_ALLOWED_PROTOCOLS: list[str] = ["http", "https", "mailto"]

# CSS scrub: executed BEFORE bleach sees the HTML so even if a `style`
# attribute sneaks through (it doesn't — we strip `style` below) the
# dangerous value is already gone. We also strip `style=""` entirely by
# leaving it out of _ALLOWED_ATTRS — belt-and-braces with this regex.
_CSS_DANGER = re.compile(
    r"(url\s*\(|@import\b|expression\s*\()",
    re.IGNORECASE,
)


def _strip_css_payloads(html: str) -> str:
    """Remove `style="...url(...)..."` and similar fragments.

    We intentionally over-scrub: any `style="…"` where the content matches
    the danger pattern becomes `style=""`. Since the tag allowlist then
    drops the attribute entirely (not in _ALLOWED_ATTRS), the net effect
    is just defence-in-depth for when the allowlist config drifts.
    """

    def _scrub(match: re.Match[str]) -> str:
        inside = match.group(2)
        if _CSS_DANGER.search(inside):
            return f'{match.group(1)}""'
        return match.group(0)

    return re.sub(
        r'(\sstyle\s*=\s*)(".*?"|\'.*?\')',
        _scrub,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


_md = MarkdownIt("commonmark", {"html": True, "breaks": False, "linkify": True})
_md.enable("table")


def render_safe_html(markdown_text: str) -> str:
    """Render a markdown string to sanitised HTML."""
    raw_html = _md.render(markdown_text or "")
    return sanitize_html(raw_html)


def sanitize_html(raw_html: str) -> str:
    """Apply the bleach allowlist + CSS scrub to an HTML fragment."""
    scrubbed = _strip_css_payloads(raw_html)
    cleaned = bleach.clean(
        scrubbed,
        tags=list(_ALLOWED_TAGS),
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    # bleach.linkify is intentionally NOT called — we let markdown-it's
    # linkify pass handle that server-side so the output is deterministic
    # across the two entry points (render_safe_html + sanitize_html).
    if cleaned != scrubbed:
        MESSAGE_SANITIZE_REJECTIONS.inc()
    return cleaned  # type: ignore[no-any-return]


__all__ = ["render_safe_html", "sanitize_html"]
