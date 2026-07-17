"""Bundle import/export (§31, Phase 4) — Anthropic Agent Skills `.zip` in, `Skill` out.

The zip is a request body written by a stranger (Q-2), so every rule rejects and nothing
repairs. Three constraints shape the module and their full rationale is in the dossier,
`docs/tasks/2026-07-16-agent-skills/spec.md` — cited here, not restated:

  * Size counters come from inflation, never from the central-directory headers the
    attacker wrote (§8 item 8 / AC-32).
  * A bundle is scanned whole before any row is created (AC-25 / Q-18); the inline scan is
    the gate, not the `scan_status` writer.
  * Import must run through `SkillsFacade.import_bundle`, which commits and then enqueues a
    scan per file. Calling `BundleService.import_bundle` directly leaves every file
    `pending` under AC-34's fail-closed gate, i.e. a permanently unreadable skill (D-60) —
    which is why the return type carries the files it created, not just the skill.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import stat
import uuid
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.skills.application.file_service import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
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
from contexts.skills.domain.readability import assert_readable
from contexts.skills.domain.text_rules import (
    SKILL_BODY_PATH,
    path_collision_key,
    skill_file_path_reason,
)

_log = logging.getLogger(__name__)

# Q-17's limits. Two of the three are pinned to numbers that already exist rather than
# chosen here: the uncompressed ceiling is `_MAX_AGENT_FILES_BYTES` (`turn_engine.py:145`)
# because a bundle's files land on the same volume that constant bounds, and the per-file
# ceiling is `MAX_SKILL_FILE_BYTES` because a bundle must not be able to install a file
# the per-file API would refuse.
MAX_BUNDLE_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024

# Total entries, **`SKILL.md` included** — which is why it is `MAX_SKILL_FILES + 1` and not
# `MAX_SKILL_FILES`. `_assert_path_free` refuses an add once a skill already holds
# `MAX_SKILL_FILES`, so a skill tops out at exactly 500 files, and its export is 500 files
# plus a body. A flat 500 here rejected that bundle at 501 > 500: the exporter emitting
# precisely what its own importer refuses, which is the one thing this constant exists to
# prevent. It was a comment asserting the invariant it broke — the off-by-one is between
# "files" and "entries", and only the arithmetic notices.
MAX_BUNDLE_ENTRIES = MAX_SKILL_FILES + 1

MAX_COMPRESSION_RATIO = 100

# The ratio rule needs a floor or it rejects honest bundles. A 30 KB `SKILL.md` of
# ordinary prose deflates to ~300 bytes — a 100:1 ratio on a file nobody would call a
# bomb — because ratio is a *property of text*, not of malice, at small sizes. A bomb has
# to inflate to hurt, so the ratio is only consulted once the bundle has already produced
# more than this much output, and below it the absolute caps are the whole control.
_RATIO_FLOOR_BYTES = 4 * 1024 * 1024

_INFLATE_CHUNK = 64 * 1024

# The only two methods a skill bundle may use — the same two `write_bundle` emits. Rejecting
# anything else *before* `zf.open` is what lets the read arm below catch a narrow, measured
# set of exceptions: an unknown method would raise `NotImplementedError` and a bz2/lzma
# entry on an image without those modules would raise a bare `RuntimeError`, and catching
# `RuntimeError` broadly would relabel a genuine bug ("dictionary changed size…", "Event
# loop is closed") as "the bundle is corrupt". Validating the method up front is the
# reject-don't-guess rule the rest of the module already follows.
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

# Scans and MinIO reads fan out with a bounded concurrency: a 500-file bundle otherwise
# serialises 500 clamd round-trips (or 500 MinIO GETs) on one request, and unbounded
# concurrency would open 500 connections at once. The cap trades a little latency for both.
_IO_CONCURRENCY = 8

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
class ImportResult:
    """What an import produced, and what its caller still owes.

    `files` is here for one reason: the caller must enqueue a scan per file after it
    commits, and it cannot do that without their ids. Returning only the skill made that
    step invisible and it was silently skipped — every imported skill unreadable forever.
    """

    skill: Skill
    files: tuple[SkillFile, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedBundle:
    manifest: SkillManifest
    files: tuple[BundleEntry, ...]
    warnings: tuple[str, ...]
    # The bytes that actually arrived, not `manifest.body`. The scanner must see what the
    # bundle carried — frontmatter included — rather than the fraction the parser kept.
    # Required, not defaulted: `_assert_clean` feeds this to the scanner as the SKILL.md
    # payload, so a `b""` default would let a construction path that forgot it scan nothing
    # and pass the gate on the one file the model is most certain to read.
    skill_md_bytes: bytes


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
    if info.compress_type not in _SUPPORTED_COMPRESSION:
        # Rejected before `zf.open`, so the arm below never has to catch the
        # `NotImplementedError`/`RuntimeError` this would otherwise raise (see
        # `_SUPPORTED_COMPRESSION`).
        raise BundleInvalid(
            f"bundle entry {info.filename!r} uses an unsupported compression method",
            path=info.filename,
        )
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
    except (zipfile.BadZipFile, EOFError, zlib.error) as exc:
        # The arm a forged size header lands in. CPython's `zipfile` bounds a read at the
        # entry's *declared* uncompressed size, so a 1 MB-declaring, 200 MB-inflating entry
        # never inflates 200 MB — the read stops and the CRC check fails here. `zlib.error`
        # (a corrupt deflate payload) is not a `BadZipFile` and `zipfile` does not wrap it;
        # both were measured, not guessed (D-56/D-61). An earlier draft's `ValueError`
        # caught nothing zipfile emits, and its `RuntimeError` was too broad — the
        # compression pre-check above removes the need for either. None of these shadows a
        # `BundleInvalid` from the loop body: `SkillError` is not an ancestor of them.
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
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        # `UnicodeDecodeError` is not hypothetical and is not a `BadZipFile`:
        # `_RealGetContents` does a bare `filename.decode('utf-8')` when an entry sets the
        # UTF-8 name flag, with no error handling. So a two-byte header edit — set flag
        # 0x800, put invalid UTF-8 in the name — crashes the constructor, and every rule
        # below is unreachable. `_entry_path`'s non-UTF-8 arm was written for this exact
        # attack and never sees it: the failure is upstream, before we hold a ZipInfo.
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
    ) -> ImportResult:
        """Import one bundle. **Call this through `SkillsFacade.import_bundle`, not directly.**

        Ordering is the contract: validate, then scan **every** entry, then write. Nothing
        reaches the database until the last byte has been cleared (AC-25).

        The files come back `pending` and their scans are the caller's to enqueue after it
        commits — see the module docstring for why that cannot happen here.
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

        # `add_many`, not a loop of `add`: the per-file `add` re-lists the skill's paths on
        # every call, which is O(n^2) over a bundle (the D-45 N+1, one level out). Mime is
        # normalised inside `_store_one`, so the raw octet-stream default is fine here.
        created = await self._files.add_many(
            skill=skill,
            files=[(entry.path, entry.data, "application/octet-stream") for entry in parsed.files],
            scan_enabled=scan_enabled,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        return ImportResult(skill=skill, files=tuple(created), warnings=parsed.warnings)

    async def export_bundle(self, skill: Skill) -> bytes:
        """Pack a skill's current state (Q-21). Deterministic over Q-30's byte set.

        Reads bytes back from MinIO rather than trusting `skill_files.sha256`: the row
        records what was stored, and the export has to carry what *is* stored.
        """
        files = await self._files.list_for_skill(skill.id)

        # Fail closed before packing: a quarantined (or still-pending) file makes the whole
        # skill unreadable to `read_skill` (AC-34), and an export must not become the side
        # door that ships those bytes anyway — an org/platform skill is exported by members
        # who never uploaded it, and §6 routes the .zip to a presigned URL. Same gate,
        # applied to the second reader of a skill's files.
        assert_readable(skill, files)

        sem = asyncio.Semaphore(_IO_CONCURRENCY)

        async def _read(f: SkillFile) -> tuple[str, bytes]:
            async with sem:
                return f.path, await self._files.read_bytes(f)

        # No sort here: `write_bundle` sorts its own input, so the read order does not reach
        # the output and the fetches are free to fan out.
        payloads = await asyncio.gather(*(_read(f) for f in files))
        return write_bundle(skill_md=render_skill_md(manifest_of(skill)), files=list(payloads))

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

        sem = asyncio.Semaphore(_IO_CONCURRENCY)

        async def _scan(path: str, blob: bytes) -> tuple[str, bool, str | None]:
            async with sem:
                result = await scanner.scan(blob)
            return path, result.clean, result.threat_name

        # Fanned out, but the verdict is still read in payload order so the rejection names
        # the same entry every time — the whole bundle is rejected regardless of which scan
        # finishes first, so order is a determinism nicety, not a correctness one.
        verdicts = await asyncio.gather(*(_scan(path, blob) for path, blob in payloads))
        for path, clean, threat in verdicts:
            if not clean:
                _log.warning("bundle import rejected — %s quarantined (threat=%s)", path, threat)
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
