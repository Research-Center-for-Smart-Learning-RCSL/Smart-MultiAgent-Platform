"""The dedicated `SKILL.md` parser (Q-29). Pure, no IO, table-driven.

`prompt_loader.py` — the §9.2 parser an earlier draft proposed reusing — was deleted with
that mechanism in Phase 1, so the "reuse it" option is gone rather than merely rejected.
It was never viable: its key charset had no hyphen (`allowed-tools:` raised), it returned
`dict[str, str]` so no list syntax existed, and its separator regex split on every `---`
thematic break in a body. PyYAML is the other road not taken (§2): tags, aliases, and
billion-laughs are a larger attack surface than a six-key header needs.

**The key policy is three-way, not two-way**, and that is the whole design (Q-29):

  * **recognized** — mapped onto :class:`SkillManifest` fields.
  * **reserved** — server-assigned state. `BundleInvalid`, naming the key.
  * **anything else** — *tolerated*: preserved into `skills.extra_frontmatter`, ignored
    semantically, surfaced as one import warning. A flat allowlist was measured against
    the 42 real `SKILL.md` files on this machine and **rejected 17 of them (40%)**.
    Rejecting unknown keys is the *more* forward-incompatible choice for the one field
    Anthropic controls and extends.

**Why `version` is tolerated and not reserved** (D-53, and read this before "fixing" it):
AC-24/26/29 name `version` as reserved server-assigned state. §6's own recognized list
contradicts them, and its reserved list says "`version`-**as-row-version**" — the two
senses collide on one name. Measured against the same 42 files, `version:` appears in
**13 (31%)**, always an author's semver (`0.1.0`, `1.0.0`), never a row counter. Rejecting
it by name would reject 31% of real bundles — the exact failure Q-29 exists to prevent.
So it falls through to the tolerated arm and round-trips through `extra_frontmatter`.

**`skills.version` is unreachable from this module regardless**, which is the defense §8
threat 2 actually asks for: "server-assigned fields structurally unreachable from parsed
input". :class:`SkillManifest` has no version field, no scope, no owner, no `created_by`.
A blocklist of names is weaker than a DTO that cannot express them, because the DTO also
holds for the key nobody thought to enumerate.

The value syntaxes below are not a YAML subset chosen for elegance — each one occurs in
the corpus, and the parser is measured against it, not against the spec of YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from contexts.skills.domain.errors import BundleInvalid
from contexts.skills.domain.models import MAX_DESCRIPTION_CHARS, SKILL_NAME_RE
from contexts.skills.domain.text_rules import text_rejection_reason

KEY_NAME = "name"
KEY_DESCRIPTION = "description"
KEY_ALLOWED_TOOLS = "allowed-tools"
KEY_LICENSE = "license"
KEY_REQUIRES = "x-smap-requires"

# `requires` was SMAP-invented and appears in **0 of 42** real bundles (Q-29), so it is
# namespaced out of the interop claim rather than squatting on a name Anthropic may take.
_RECOGNIZED: frozenset[str] = frozenset(
    {KEY_NAME, KEY_DESCRIPTION, KEY_ALLOWED_TOOLS, KEY_LICENSE, KEY_REQUIRES}
)

# The keys that carry lists. Only these comma-split a bare scalar: `description: Does
# things, and more` must stay one string, so the split is per-key and never global.
_LIST_KEYS: frozenset[str] = frozenset({KEY_ALLOWED_TOOLS, KEY_REQUIRES})

# Server-assigned state. `version` is deliberately absent — see the module docstring.
_RESERVED: frozenset[str] = frozenset(
    {
        "scope",
        "id",
        "created_by",
        "source",
        "agent_id",
        "project_id",
        "org_id",
        "bundle_sha256",
    }
)
_RESERVED_PREFIXES: tuple[str, ...] = ("owner_",)

# A tolerated value is stored and re-emitted, so it needs a bound of its own. The
# description cap is reused rather than invented: it is the largest any header field is
# allowed to be, and a tolerated key has no claim to more room than the field the model
# actually reads.
_MAX_EXTRA_VALUE_CHARS = MAX_DESCRIPTION_CHARS
_MAX_EXTRA_KEY_CHARS = 64

_FENCE = "---"
# `chr()`, never a literal — the same rule `text_rules.py` builds its sets by. A literal
# here would put an invisible character in this file, which is the Trojan-Source attack
# aimed at the reviewer instead of the model.
_BOM = chr(0xFEFF)

# Column 0 only. A continuation line of a multi-line value is indented (§6), so anchoring
# here is what keeps `scope: platform` on an indented continuation from becoming a key.
_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(.*)$")
_SEQ_ITEM_RE = re.compile(r"^\s*-\s*(.*)$")


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """One parsed `SKILL.md`. The DTO §6 requires, and **never splatted onto a model**.

    Note what is absent and cannot be added by any frontmatter key: `scope`, `version`,
    `source`, `created_by`, `bundle_sha256`, every owner id. That absence is the control
    (§8 threat 2) — the importer must name each field it copies, so a key the parser has
    never heard of has no route to a column.
    """

    name: str
    description: str
    body: str
    allowed_tools: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    license: str | None = None
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _reserved_reason(key: str) -> str | None:
    if key in _RESERVED or key.startswith(_RESERVED_PREFIXES):
        return f"frontmatter key {key!r} is server-assigned state and cannot be imported"
    return None


def _unquote(text: str) -> str:
    """Strip one matched pair of outer quotes. Not a YAML unescaper.

    Deliberately shallow: the corpus quotes to protect a `:` or a leading `[`, never to
    escape. Interpreting `\\n` here would reintroduce the newline the charset rule exists
    to keep out of the index, so escapes stay literal and the rule stays enforceable.
    """
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _fold(text: str) -> str:
    """Fold a multi-line value onto one line, YAML-style.

    The marketplace writes indented multi-line quoted descriptions (§6), and the index
    renders one skill per line — so folding is what makes those importable at all. It runs
    *before* the charset rule, so a folded value that still holds a newline is a real
    violation rather than an artifact of the source layout.
    """
    return re.sub(r"[ \t]*\n[ \t]*", " ", text).strip()


def _quoted_end(text: str, quote: str, start: int) -> int | None:
    i = start
    while i < len(text):
        ch = text[i]
        if quote == '"' and ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i
        i += 1
    return None


def _flow_end(text: str, start: int) -> int | None:
    i, quote = start, None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "]":
            return i
        i += 1
    return None


def _split_items(inner: str, *, key: str) -> list[str]:
    """Split a comma-separated list body, honoring quotes.

    `allowed-tools: Read, Grep, Bash(git:*)` is the shape that forces this: the last
    element carries a colon and parentheses, so the split has to survive a value that was
    never quoted in the first place.
    """
    items: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if quote is not None:
        raise BundleInvalid(f"frontmatter key {key!r} has an unterminated quote", key=key)
    items.append("".join(buf))
    return [_unquote(item.strip()) for item in items if item.strip()]


def _gather_indented_block(lines: list[str], start: int, *, key: str) -> tuple[str | None, int]:
    """Gather an indented scalar block written *below* its key. Returns (text, next_index).

    This is §6's "indented multi-line quoted scalars for `description`, which the
    official marketplace uses" — and it is not the same-line shape: the key's own line
    ends at the colon and the quote opens on the next. Exactly one file in 42 writes it,
    and it rejected the first version of this parser, which is the argument for measuring
    the corpus rather than reasoning about the format.
    """
    j = start
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines):
        return None, start
    if not lines[j].startswith((" ", "\t")) or _SEQ_ITEM_RE.match(lines[j]):
        return None, start

    collected: list[str] = []
    while j < len(lines):
        current = lines[j]
        if not current.strip():
            j += 1
            continue
        # A column-0 line ends the block. Indentation is the only thing holding these
        # lines together, so a dedent is the terminator.
        if not current.startswith((" ", "\t")):
            break
        collected.append(current)
        j += 1

    text = _fold("\n".join(collected))
    if text and text[0] in "\"'":
        quote = text[0]
        end = _quoted_end(text, quote, 1)
        if end is None:
            raise BundleInvalid(f"frontmatter key {key!r} has an unterminated quoted value", key=key)
        if text[end + 1 :].strip():
            raise BundleInvalid(f"frontmatter key {key!r} has trailing text after its quoted value", key=key)
        return text[1:end], j
    return text, j


def _gather_value(lines: list[str], idx: int, rest: str, *, key: str) -> tuple[Any, int]:
    """Parse one key's value, consuming continuation lines. Returns (value, next_index).

    `value` is `str | list[str]`. Which one is decided by *syntax* here and reconciled
    against the key's expected type by the caller, so a shape mismatch names the key
    rather than silently coercing.
    """
    head = rest.strip()

    # `key:` with nothing after it — a block sequence, an indented block, or empty.
    if not head:
        items: list[str] = []
        j = idx + 1
        while j < len(lines):
            line = lines[j]
            if not line.strip():
                j += 1
                continue
            if _KEY_RE.match(line):
                break
            m = _SEQ_ITEM_RE.match(line)
            if m is None:
                break
            items.append(_unquote(m.group(1).strip()))
            j += 1
        if items:
            return items, j
        block, after = _gather_indented_block(lines, idx + 1, key=key)
        if block is not None:
            return block, after
        # `allowed-tools:` with no items is `[]`, never `[""]` — 6 of 42 files (§6). The
        # caller turns this into `""` for a scalar key.
        return [], j

    # Flow sequence, possibly spanning lines.
    if head.startswith("["):
        text = head
        j = idx
        end = _flow_end(text, 1)
        while end is None:
            j += 1
            if j >= len(lines):
                raise BundleInvalid(f"frontmatter key {key!r} has an unterminated flow sequence", key=key)
            text = f"{text}\n{lines[j]}"
            end = _flow_end(text, 1)
        if text[end + 1 :].strip():
            raise BundleInvalid(f"frontmatter key {key!r} has trailing text after its flow sequence", key=key)
        return _split_items(_fold(text[1:end]), key=key), j + 1

    # Quoted scalar, possibly spanning lines. Gathering is greedy to the closing quote
    # even across a line that *looks* like a key — which is the safe direction: a forged
    # `scope: platform` inside an open quote stays a description and never becomes a key.
    if head[0] in "\"'":
        quote = head[0]
        text = head
        j = idx
        end = _quoted_end(text, quote, 1)
        while end is None:
            j += 1
            if j >= len(lines):
                raise BundleInvalid(f"frontmatter key {key!r} has an unterminated quoted value", key=key)
            text = f"{text}\n{lines[j]}"
            end = _quoted_end(text, quote, 1)
        if text[end + 1 :].strip():
            raise BundleInvalid(f"frontmatter key {key!r} has trailing text after its quoted value", key=key)
        return _fold(text[1:end]), j + 1

    # Bare scalar. Comma-splitting is the caller's business: it depends on the key.
    return head, idx + 1


def _as_scalar(value: Any, *, key: str) -> str:
    if isinstance(value, list):
        if not value:
            return ""
        raise BundleInvalid(f"frontmatter key {key!r} expects a single value, not a list", key=key)
    return str(value)


def _as_list(value: Any, *, key: str) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(value)
    text = str(value).strip()
    if not text:
        return ()
    # A *quoted* scalar is one string by YAML's rule, so it stays one item; only bare
    # text comma-splits (§6).
    return tuple(_split_items(text, key=key))


def _reject(value: str, *, key: str, max_chars: int) -> None:
    reason = text_rejection_reason(value, max_chars=max_chars)
    if reason is not None:
        raise BundleInvalid(f"frontmatter key {key!r} {reason}", key=key)


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """Split a `SKILL.md` into its frontmatter lines and its verbatim body.

    **Only the document's leading `---` block is frontmatter.** A `---` later in the file
    is a markdown thematic break and belongs to the body — the trap `prompt_loader`'s
    `^---$` MULTILINE regex fell into, which is why a body could not carry a horizontal
    rule without being silently truncated.
    """
    normalized = text.lstrip(_BOM).replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != _FENCE:
        raise BundleInvalid("SKILL.md does not open with a `---` frontmatter block")
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            # Verbatim: no leading-blank strip. `---\n\n# Title` must export back to
            # itself byte-for-byte (AC-26), and the blank line is part of the body.
            return lines[1:i], "\n".join(lines[i + 1 :])
    raise BundleInvalid("SKILL.md frontmatter block is never closed")


def parse_skill_md(text: str) -> SkillManifest:
    """Parse a `SKILL.md` document. Raises :class:`BundleInvalid` with a named key.

    The document is the untrusted half of a bundle, so every failure here is a rejection
    and never a repair: a silently corrected header produces a skill whose author did not
    write what the index shows.
    """
    lines, body = split_frontmatter(text)

    raw: dict[str, Any] = {}
    unknown: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # §6: `#` at line start is a comment. Anchored at column 0 deliberately — a `#`
        # inside a value (`description: Use # for headings`) is text, not a comment, and
        # this parser has no business editing an author's prose.
        if line.startswith("#"):
            i += 1
            continue
        m = _KEY_RE.match(line)
        if m is None:
            raise BundleInvalid(f"cannot parse frontmatter line {i + 1}: {line.strip()!r}")
        key = m.group(1)
        reserved = _reserved_reason(key)
        if reserved is not None:
            raise BundleInvalid(reserved, key=key)
        if key in raw:
            # `prompt_loader._parse_frontmatter` silently last-wins here. That is a trap,
            # not a precedent: two `description:` lines mean the author does not know
            # which one the model will read, and neither do we.
            raise BundleInvalid(f"duplicate frontmatter key {key!r}", key=key)
        value, i = _gather_value(lines, i, m.group(2), key=key)
        raw[key] = value
        if key not in _RECOGNIZED:
            unknown.append(key)

    for required in (KEY_NAME, KEY_DESCRIPTION):
        if required not in raw:
            raise BundleInvalid(f"SKILL.md frontmatter has no {required!r}", key=required)

    name = _as_scalar(raw[KEY_NAME], key=KEY_NAME)
    if not SKILL_NAME_RE.match(name):
        raise BundleInvalid(
            f"skill name {name!r} must be lowercase alphanumeric with hyphens and " f"at most 64 characters",
            key=KEY_NAME,
        )

    description = _as_scalar(raw[KEY_DESCRIPTION], key=KEY_DESCRIPTION)
    _reject(description, key=KEY_DESCRIPTION, max_chars=MAX_DESCRIPTION_CHARS)
    if not description.strip():
        raise BundleInvalid("frontmatter key 'description' is empty", key=KEY_DESCRIPTION)

    allowed_tools = _as_list(raw.get(KEY_ALLOWED_TOOLS, ()), key=KEY_ALLOWED_TOOLS)
    for tool in allowed_tools:
        _reject(tool, key=KEY_ALLOWED_TOOLS, max_chars=MAX_DESCRIPTION_CHARS)

    requires = _as_list(raw.get(KEY_REQUIRES, ()), key=KEY_REQUIRES)
    for req in requires:
        _reject(req, key=KEY_REQUIRES, max_chars=MAX_DESCRIPTION_CHARS)

    license_value: str | None = None
    if KEY_LICENSE in raw:
        license_value = _as_scalar(raw[KEY_LICENSE], key=KEY_LICENSE)
        _reject(license_value, key=KEY_LICENSE, max_chars=MAX_DESCRIPTION_CHARS)

    # Tolerated keys are validated too. They are display-only and semantically ignored,
    # which is exactly what makes them a display-injection surface (the AC-30 argument for
    # `license` and `allowed_tools`, applied to the arm holding the *unknown* keys) — and
    # they are re-emitted on export, so an unchecked newline here would forge a key in a
    # file SMAP itself writes.
    extra: dict[str, Any] = {}
    for key in unknown:
        value = raw[key]
        _reject(key, key=key, max_chars=_MAX_EXTRA_KEY_CHARS)
        if isinstance(value, list):
            for item in value:
                _reject(item, key=key, max_chars=_MAX_EXTRA_VALUE_CHARS)
            extra[key] = list(value)
        else:
            scalar = str(value)
            _reject(scalar, key=key, max_chars=_MAX_EXTRA_VALUE_CHARS)
            extra[key] = scalar

    warnings: list[str] = []
    if unknown:
        warnings.append(
            "unrecognized frontmatter keys preserved but not interpreted: " + ", ".join(sorted(unknown))
        )

    return SkillManifest(
        name=name,
        description=description,
        body=body,
        allowed_tools=allowed_tools,
        requires=requires,
        license=license_value,
        extra_frontmatter=extra,
        warnings=tuple(warnings),
    )


__all__ = [
    "KEY_ALLOWED_TOOLS",
    "KEY_DESCRIPTION",
    "KEY_LICENSE",
    "KEY_NAME",
    "KEY_REQUIRES",
    "SkillManifest",
    "parse_skill_md",
    "split_frontmatter",
]
