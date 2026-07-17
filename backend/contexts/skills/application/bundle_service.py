"""Bundle import (§31, Phase 4) — Anthropic Agent Skills `.zip` in, `Skill` out.

**The zip is a request body written by a stranger** (Q-2 is precisely what makes the
author one), so every rule here rejects and nothing repairs. A silently sanitised path is
one `SKILL.md` references by its original spelling and can no longer find — the
confabulation Q-18 rejects partial imports to avoid.

Two rules carry most of the weight:

**Counters come from inflation, never from headers.** `ZipInfo.file_size` and
`compress_size` live in the central directory, which the attacker wrote. A header-based
check passes a zip declaring 1 MB and inflating to 40 GB (§8 item 8 / AC-32), so every
byte here is counted as it comes out of the decompressor and the budget trips mid-stream.
The one size we can trust is `len(data)` — we hold those bytes — which is why the ratio is
computed against it rather than against a per-entry `compress_size`.

**Scanning precedes creation.** AC-25/Q-18 reject a bundle whole when one file is
quarantined, and "whole" is only meaningful before rows exist. The async `skill_scan_file`
worker cannot deliver that — it runs after `skill_files` rows are written, which is a
partial import by definition — so the import path scans inline and creates nothing until
every entry is clean. The worker still re-scans afterwards and remains the standing
authority on `scan_status`; this gate does not write that column (FU-45 owns the
double-scan cost).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import stat
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.skills.application.file_service import (
    MAX_SKILL_FILE_BYTES,
    SkillFileService,
)
from contexts.skills.application.skill_md import (
    KEY_LICENSE,
    SkillManifest,
    parse_skill_md,
    render_skill_md,
)
from contexts.skills.application.skill_service import SkillService
from contexts.skills.domain.errors import BundleInvalid, BundleQuarantined
from contexts.skills.domain.models import Skill, SkillFile, SkillScope, SkillSource
from contexts.skills.domain.text_rules import (
    SKILL_BODY_PATH,
    path_collision_key,
    skill_file_path_reason,
)
from shared_kernel.text_extraction.parsers import normalise_mime

_log = logging.getLogger(__name__)

# Q-17's limits. Two of the three are pinned to numbers that already exist rather than
# chosen here: the uncompressed ceiling is `_MAX_AGENT_FILES_BYTES` (`turn_engine.py:145`)
# because a bundle's files land on the same volume that constant bounds, and the per-file
# ceiling is `MAX_SKILL_FILE_BYTES` because a bundle must not be able to install a file
# the per-file API would refuse.
MAX_BUNDLE_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024

# Total entries, `SKILL.md` included, so a bundle can carry at most 499 files — strictly
# under `MAX_SKILL_FILES`. The inequality is deliberate and one-directional: the exporter
# must never emit a skill its own importer would reject.
MAX_BUNDLE_ENTRIES = 500

MAX_COMPRESSION_RATIO = 100

# The ratio rule needs a floor or it rejects honest bundles. A 30 KB `SKILL.md` of
# ordinary prose deflates to ~300 bytes — a 100:1 ratio on a file nobody would call a
# bomb — because ratio is a *property of text*, not of malice, at small sizes. A bomb has
# to inflate to hurt, so the ratio is only consulted once the bundle has already produced
# more than this much output, and below it the absolute caps are the whole control.
_RATIO_FLOOR_BYTES = 4 * 1024 * 1024

_INFLATE_CHUNK = 64 * 1024

# Bit 0 of the general-purpose flags: the entry is encrypted. Rejected rather than
# attempted — bytes we cannot read are bytes ClamAV cannot scan, and AC-25's gate would
# pass them by never seeing them.
_FLAG_ENCRYPTED = 0x1
# Bit 11: the filename is UTF-8. Without it `zipfile` decodes as CP437 and *guesses*.
_FLAG_UTF8_NAME = 0x800

# Q-10/AC-27. `code_exec` containers run `network_mode="none"` (`docker_runsc.py:701`), so
# a script that reaches for the network fails at run time inside a sandbox with no route
# out. That is a **compatibility warning, not a rejection**: the isolation is SEC-C1 and
# the skill may well work without its network path, so refusing the import would decide
# for the author something only they can decide.
_NETWORK_HINTS = re.compile(
    r"""(?x)
    \bimport\s+(requests|httpx|aiohttp|urllib|socket|http\.client|ftplib|telnetlib|smtplib)\b
    | \bfrom\s+(requests|httpx|aiohttp|urllib|urllib\.request|socket|http|http\.client)\s+import\b
    | \burlopen\s*\(
    | \bsocket\.(socket|create_connection)\s*\(
    | \brequests\.(get|post|put|delete|head|patch)\s*\(
    | \bcurl\b | \bwget\b
    """
)


@dataclass(frozen=True, slots=True)
class BundleEntry:
    """One non-`SKILL.md` file, with the bytes that actually came out of the zip."""

    path: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ParsedBundle:
    manifest: SkillManifest
    files: tuple[BundleEntry, ...]
    warnings: tuple[str, ...]
    # The bytes that actually arrived, not `manifest.body`. The scanner must see what the
    # bundle carried — frontmatter included — rather than the fraction the parser kept.
    skill_md_bytes: bytes = b""


class _InflationBudget:
    """The running counter that makes a lying header useless (AC-32).

    Constructed from `len(data)` — the compressed size we measured ourselves — never from
    a field the bundle declares about itself.
    """

    def __init__(self, *, compressed_bytes: int) -> None:
        self._compressed = max(compressed_bytes, 1)
        self.inflated = 0

    def consume(self, count: int) -> None:
        self.inflated += count
        if self.inflated > MAX_BUNDLE_UNCOMPRESSED_BYTES:
            raise BundleInvalid(f"bundle inflates past the {MAX_BUNDLE_UNCOMPRESSED_BYTES} byte limit")
        if self.inflated > _RATIO_FLOOR_BYTES and self.inflated > self._compressed * MAX_COMPRESSION_RATIO:
            raise BundleInvalid(f"bundle exceeds the {MAX_COMPRESSION_RATIO}:1 compression ratio limit")


def _assert_not_a_link(info: zipfile.ZipInfo) -> None:
    # The external attributes' high 16 bits are the Unix mode when the zip was made on a
    # Unix host. A symlink entry's "content" is its target path, so an importer that
    # treated it as a file would write the target string into MinIO, and one that honored
    # it would follow a link out of the bundle.
    if info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16):
        raise BundleInvalid(f"bundle entry {info.filename!r} is a symlink", path=info.filename)


def _entry_path(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & _FLAG_ENCRYPTED:
        raise BundleInvalid(
            f"bundle entry {info.filename!r} is encrypted and cannot be scanned",
            path=info.filename,
        )
    name = info.filename
    if not (info.flag_bits & _FLAG_UTF8_NAME) and not name.isascii():
        # `zipfile` already decoded this as CP437 and produced a guess. Rejecting is the
        # only honest move: importing it would store mojibake as the path `SKILL.md`
        # references, and this is a zh-TW product where non-ASCII paths are ordinary.
        raise BundleInvalid(f"bundle entry {name!r} has a non-UTF-8 path", path=name)
    return name


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _InflationBudget) -> bytes:
    """Inflate one entry, counting every chunk as it lands.

    The per-file cap is checked *inside* the loop for the same reason the budget is: a
    32 MB limit enforced after `read()` returns has already allocated whatever the entry
    inflated to.
    """
    out = bytearray()
    try:
        with zf.open(info) as fh:
            while True:
                chunk = fh.read(_INFLATE_CHUNK)
                if not chunk:
                    break
                budget.consume(len(chunk))
                out.extend(chunk)
                if len(out) > MAX_SKILL_FILE_BYTES:
                    raise BundleInvalid(
                        f"bundle entry {info.filename!r} exceeds the {MAX_SKILL_FILE_BYTES} "
                        f"byte per-file limit",
                        path=info.filename,
                    )
    except (zipfile.BadZipFile, EOFError, ValueError) as exc:
        # **This is the arm a forged size header actually lands in**, and it is not
        # decoration. CPython's `zipfile` bounds a read at the entry's *declared*
        # uncompressed size, so a header claiming 1 MB over a 200 MB payload never
        # inflates 200 MB: the read stops at 1 MB and the CRC check fails here. AC-32
        # describes that zip as "rejected mid-inflate by the streaming counter" — on this
        # runtime the counter never sees it, and without this arm the rejection was a
        # `BadZipFile` escaping as a 500 instead of a 422 (D-56). The counter still earns
        # its keep against the honest bomb and the ratio rule, and it is the backstop if a
        # future decompressor stops bounding the read.
        raise BundleInvalid(
            f"bundle entry {info.filename!r} is corrupt or its declared size is wrong: {exc}",
            path=info.filename,
        ) from exc
    return bytes(out)


def _network_warning(files: list[BundleEntry]) -> str | None:
    offenders = sorted(
        f.path for f in files if f.path.startswith("scripts/") and _NETWORK_HINTS.search(_as_text(f.data))
    )
    if not offenders:
        return None
    return (
        "scripts appear to use the network, which skill scripts cannot reach: "
        "code_exec containers run with no network route (Q-10). Affected: " + ", ".join(offenders)
    )


def _as_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # A binary under `scripts/` is not a script we can read for hints. It is still a
        # legal entry, so this is a miss on the warning, not a rejection.
        return ""


def read_bundle(data: bytes) -> ParsedBundle:
    """Validate and inflate a bundle. Raises :class:`BundleInvalid` naming the entry.

    Pure but for the CPU: no DB, no MinIO, no scanner. That is what lets AC-24's whole
    rejection matrix be a table test over bytes.
    """
    if len(data) > MAX_BUNDLE_COMPRESSED_BYTES:
        raise BundleInvalid(f"bundle exceeds the {MAX_BUNDLE_COMPRESSED_BYTES} byte compressed limit")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleInvalid(f"not a readable zip archive: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_BUNDLE_ENTRIES:
            raise BundleInvalid(f"bundle carries {len(infos)} entries, past the {MAX_BUNDLE_ENTRIES} limit")
        if not infos:
            raise BundleInvalid("bundle is empty")

        budget = _InflationBudget(compressed_bytes=len(data))
        seen: dict[str, str] = {}
        body_text: str | None = None
        skill_md_bytes = b""
        files: list[BundleEntry] = []

        for info in infos:
            _assert_not_a_link(info)
            path = _entry_path(info)

            collision = path_collision_key(path)
            if collision in seen:
                # Two paths differing only by case import cleanly on Linux prod and
                # destroy one another on a Windows dev box (§8 item 8).
                raise BundleInvalid(
                    f"bundle entries {seen[collision]!r} and {path!r} collide " f"case-insensitively",
                    path=path,
                )
            seen[collision] = path

            if path == SKILL_BODY_PATH:
                skill_md_bytes = _read_entry(zf, info, budget)
                try:
                    body_text = skill_md_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise BundleInvalid("SKILL.md is not valid UTF-8") from exc
                continue

            reason = skill_file_path_reason(path)
            if reason is not None:
                # One predicate for bundle paths and API paths alike. A rule enforced at
                # only some entry points is not a rule, and this is the entry point whose
                # author is a stranger.
                raise BundleInvalid(f"bundle entry {path!r} {reason}", path=path)
            files.append(BundleEntry(path=path, data=_read_entry(zf, info, budget)))

    if body_text is None:
        raise BundleInvalid(f"bundle has no {SKILL_BODY_PATH} at its root")

    manifest = parse_skill_md(body_text)

    warnings = list(manifest.warnings)
    network = _network_warning(files)
    if network is not None:
        warnings.append(network)

    return ParsedBundle(
        manifest=manifest,
        files=tuple(sorted(files, key=lambda f: f.path)),
        warnings=tuple(warnings),
        skill_md_bytes=skill_md_bytes,
    )


# Every timestamp in an exported bundle. A zip records mtime per entry, so exporting the
# same skill twice would otherwise produce different bytes and Q-19's determinism would be
# false for a reason that has nothing to do with the skill. 1980-01-01 is the zip epoch —
# the earliest a DOS timestamp can express, so no reader sees it as a corrupt field.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# `0o100644 << 16`: a regular file, rw-r--r--. Fixed for the same reason as the timestamp,
# and never `0o755` even for `scripts/` — the sandbox invokes the interpreter on the path
# (§4.4), so an execute bit would be decoration on a volume that never honors it.
_ZIP_FILE_ATTR = 0o100644 << 16


def write_bundle(*, skill_md: str, files: Sequence[tuple[str, bytes]]) -> bytes:
    """Pack a bundle deterministically. The same inputs always produce the same bytes.

    Determinism is Q-19's requirement and it is entirely about what is *excluded*:
    timestamps, file modes, and entry order are all things a zip records and a skill does
    not have. `SKILL.md` leads (a reader unzipping by hand meets the body first) and the
    rest sort by path.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entries: list[tuple[str, bytes]] = [
            (SKILL_BODY_PATH, skill_md.encode("utf-8")),
            *sorted(files, key=lambda pair: pair[0]),
        ]
        for path, data in entries:
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = _ZIP_FILE_ATTR
            # `create_system = 3` (Unix) so `external_attr` reads back as the mode we set
            # rather than as DOS attribute bits — the same field `_assert_not_a_link`
            # interprets on the way in.
            info.create_system = 3
            zf.writestr(info, data)
    return buf.getvalue()


def manifest_of(skill: Skill) -> SkillManifest:
    """The exportable view of a stored skill.

    **`license` has no column** (D-57). §6 enumerates every `skills` column and `license`
    is not among them, yet §6's recognized-key list and AC-30's matrix both name it, and
    Phase 1 deferred it as "arrives with bundles (Phase 4)" — this is Phase 4, and the
    column still does not exist. It rides in `extra_frontmatter` instead: no migration, it
    round-trips, and the charset rule already covers it there. Popped out here so `render`
    emits it in its declared slot exactly once rather than twice — once as a field and
    again as a tolerated key.

    It stays out of `authored_digest` because Q-30's byte set does not name it, so editing
    a license does not mark a skill diverged. That is Q-30's call, recorded rather than
    quietly widened.
    """
    extra = dict(skill.extra_frontmatter or {})
    license_value = extra.pop(KEY_LICENSE, None)
    return SkillManifest(
        name=skill.name,
        description=skill.description,
        body=skill.body,
        allowed_tools=skill.allowed_tools,
        requires=skill.requires,
        license=None if license_value is None else str(license_value),
        extra_frontmatter=extra,
    )


def _storable_frontmatter(manifest: SkillManifest) -> dict[str, Any]:
    """The inverse of `manifest_of`'s pop: fold `license` back in for storage."""
    extra = dict(manifest.extra_frontmatter)
    if manifest.license is not None:
        extra[KEY_LICENSE] = manifest.license
    return extra


def authored_digest(
    *,
    name: str,
    description: str,
    requires: tuple[str, ...],
    allowed_tools: tuple[str, ...],
    body: str,
    file_digests: dict[str, str],
) -> str:
    """The hash of a skill's **authored byte set** — Q-30's definition, and only it.

    This is the number `skills.bundle_sha256` holds and the one a "diverged" badge
    compares against (Q-20/AC-16), so it must be computable from current state at any
    time and must exclude every server-assigned field: `source`, `version`, `scope`,
    `created_by`, `bundle_sha256` itself. Hashing the uploaded zip's bytes instead would
    be simpler and wrong — zip bytes carry timestamps and compression choices, so the same
    authored content would produce a different digest on every export and every skill
    would read as diverged forever.

    `file_digests` maps path to the file's own sha256 rather than carrying bytes, because
    the caller already has the hashes and this stays O(paths).
    """
    h = hashlib.sha256()

    def field(label: str, value: str) -> None:
        # Length-prefixed, so no value can impersonate the framing of the next one. Two
        # skills differing only by where a field boundary falls must not collide.
        h.update(f"{label}:{len(value)}:".encode())
        h.update(value.encode("utf-8"))

    field("name", name)
    field("description", description)
    field("body", body)
    field("requires", "\x00".join(requires))
    field("allowed_tools", "\x00".join(allowed_tools))
    for path in sorted(file_digests):
        field("file", path)
        field("sha256", file_digests[path])
    return h.hexdigest()


def is_diverged(skill: Skill, files: Sequence[SkillFile]) -> bool:
    """Has this skill been edited since it was imported? (Q-20 / AC-16's badge.)

    An `authored` skill was never a bundle, so it has nothing to diverge *from* and the
    answer is False rather than True — the distinction `bundle_sha256 is None` carries and
    that "imported and has a hash" would have destroyed by badging every import forever.
    Phase 1 hardcoded False here because the byte set was undefined until an exporter
    existed to define it; it exists now.
    """
    if skill.bundle_sha256 is None:
        return False
    current = authored_digest(
        name=skill.name,
        description=skill.description,
        requires=skill.requires,
        allowed_tools=skill.allowed_tools,
        body=skill.body,
        file_digests={f.path: f.sha256 for f in files},
    )
    return current != skill.bundle_sha256


class BundleService:
    """Import orchestration. Owns no transaction — the caller does (the house pattern)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._skills = SkillService(db)
        self._files = SkillFileService(db)

    async def import_bundle(
        self,
        *,
        data: bytes,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[Skill, tuple[str, ...]]:
        """Import one bundle. Returns the skill and every compatibility warning.

        Ordering is the contract: validate, then scan **every** entry, then write. Nothing
        reaches the database until the last byte has been cleared (AC-25).
        """
        parsed = read_bundle(data)
        await self._assert_clean(parsed)

        scan_enabled = get_settings().security.file_scan_enabled
        digests = {f.path: hashlib.sha256(f.data).hexdigest() for f in parsed.files}

        skill = await self._skills.create(
            scope=scope,
            owner_id=owner_id,
            name=parsed.manifest.name,
            description=parsed.manifest.description,
            body=parsed.manifest.body,
            requires=parsed.manifest.requires,
            allowed_tools=parsed.manifest.allowed_tools,
            extra_frontmatter=_storable_frontmatter(parsed.manifest),
            # Named one by one, never splatted from the manifest (§8 threat 2). `scope`
            # and `owner_id` come from the router's path; `source` is decided here.
            source=SkillSource.IMPORTED,
            bundle_sha256=authored_digest(
                name=parsed.manifest.name,
                description=parsed.manifest.description,
                requires=parsed.manifest.requires,
                allowed_tools=parsed.manifest.allowed_tools,
                body=parsed.manifest.body,
                file_digests=digests,
            ),
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

        for entry in parsed.files:
            await self._files.add(
                skill=skill,
                path=entry.path,
                data=entry.data,
                mime=normalise_mime("application/octet-stream", entry.path),
                scan_enabled=scan_enabled,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        return skill, parsed.warnings

    async def export_bundle(self, skill: Skill) -> bytes:
        """Pack a skill's current state (Q-21). Deterministic over Q-30's byte set.

        Reads bytes back from MinIO rather than trusting `skill_files.sha256`: the row
        records what was stored, and the export has to carry what *is* stored.
        """
        files = await self._files.list_for_skill(skill.id)
        payloads: list[tuple[str, bytes]] = []
        for f in sorted(files, key=lambda f: f.path):
            payloads.append((f.path, await self._files.read_bytes(f)))
        return write_bundle(skill_md=render_skill_md(manifest_of(skill)), files=payloads)

    async def _assert_clean(self, parsed: ParsedBundle) -> None:
        """Scan every byte the bundle carries. One quarantine rejects the whole (Q-18).

        `SKILL.md` is scanned too, though it never becomes a `skill_files` row: it is the
        part of the bundle the model is most certain to read.
        """
        if not get_settings().security.file_scan_enabled:
            # No scanner deployed. `_initial_scan_status` already treats this as CLEAN for
            # every other path into `skill_files`, so refusing imports here would make
            # Skills the one feature that requires ClamAV.
            return

        from shared_kernel.scanning import get_scanner

        scanner = get_scanner()
        if scanner is None:
            raise RuntimeError("file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set")

        payloads: list[tuple[str, bytes]] = [
            (SKILL_BODY_PATH, parsed.skill_md_bytes),
            *((f.path, f.data) for f in parsed.files),
        ]
        for path, blob in payloads:
            result = await scanner.scan(blob)
            if not result.clean:
                _log.warning("bundle import rejected — %s quarantined (threat=%s)", path, result.threat_name)
                raise BundleQuarantined(path)


__all__ = [
    "MAX_BUNDLE_COMPRESSED_BYTES",
    "MAX_BUNDLE_ENTRIES",
    "MAX_BUNDLE_UNCOMPRESSED_BYTES",
    "MAX_COMPRESSION_RATIO",
    "BundleEntry",
    "BundleService",
    "ParsedBundle",
    "authored_digest",
    "is_diverged",
    "manifest_of",
    "read_bundle",
    "write_bundle",
]
