"""Bleach-backed allowlist sanitiser + markdown → HTML pipeline (R13.14).

Scope:
  - Strip `<script>`, event handlers, `javascript:` / `data:text/html` URIs,
    `<object>`, `<embed>`, `<iframe>`, `<form>`, `<meta http-equiv="refresh">`.
  - Drop the `style` attribute outright — it is absent from the attribute
    allowlist, so every CSS-borne payload (`url(...)`, `@import`,
    `expression(...)`) is removed at the source. bleach's allowlist is the
    sole control; there is no separate CSS regex pass (DOM-13).
  - Render markdown with `markdown-it-py` (CommonMark + tables) and run the
    output through the allowlist.

The client does a second pass via DOMPurify before displaying; server-side
sanitisation here protects export artefacts (JSON manifest includes the
sanitised HTML per R13.14) and ensures anything persisted is safe.
"""

from __future__ import annotations

from bleach.html5lib_shim import Filter
from bleach.sanitizer import Cleaner
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


class _ForceRelNoopenerFilter(Filter):
    """Force ``rel="noopener noreferrer"`` on any anchor that sets ``target``.

    Runs after the bleach sanitizer (so the injected ``rel`` is not re-stripped)
    and closes the reverse-tabnabbing gap for ``target="_blank"`` links carried
    in supplied fragments — the opened page can no longer reach ``window.opener``.
    """

    def __iter__(self):  # type: ignore[no-untyped-def]
        for token in super().__iter__():
            if token.get("name") == "a" and token.get("type") in ("StartTag", "EmptyTag"):
                data = token.get("data")
                if data and any(name == "target" for (_ns, name) in data):
                    data[(None, "rel")] = "noopener noreferrer"
            yield token


_cleaner = Cleaner(
    tags=list(_ALLOWED_TAGS),
    attributes=_ALLOWED_ATTRS,
    protocols=_ALLOWED_PROTOCOLS,
    strip=True,
    strip_comments=True,
    filters=[_ForceRelNoopenerFilter],
)


_md = MarkdownIt("commonmark", {"html": True, "breaks": False, "linkify": True})
_md.enable("table")


def render_safe_html(markdown_text: str) -> str:
    """Render a markdown string to sanitised HTML."""
    raw_html = _md.render(markdown_text or "")
    return sanitize_html(raw_html)


def sanitize_html(raw_html: str) -> str:
    """Apply the bleach allowlist to an HTML fragment.

    DOM-13: there is no separate CSS scrub. The attribute allowlist omits
    `style` (and every dangerous tag per R13.14), so bleach removes the
    attribute — and thus every CSS-borne payload — outright. The old regex
    pass gave false confidence: it never handled unquoted `style` values and
    duplicated a control bleach already enforces unconditionally.
    """
    cleaned = _cleaner.clean(raw_html)
    # bleach.linkify is intentionally NOT called — we let markdown-it's
    # linkify pass handle that server-side so the output is deterministic
    # across the two entry points (render_safe_html + sanitize_html).
    if cleaned != raw_html:
        MESSAGE_SANITIZE_REJECTIONS.inc()
    return cleaned


__all__ = ["render_safe_html", "sanitize_html"]
