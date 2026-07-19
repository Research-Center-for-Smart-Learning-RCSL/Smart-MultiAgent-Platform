"""Clear legacy `/workspace/sessions/` trees from per-agent volumes.

`docs/tasks/2026-07-19-session-dir-room-isolation`. Per-chatroom session state
(staged `inputs/`, generated `outputs/`) used to live on the per-agent volume,
which every one of that agent's kernels mounts. Any room could therefore read any
other room's attachments and artifacts, through `code_exec` or the `file` tool.

The fix moved session state to a per-`(agent, chatroom)` volume, but that is
prospective: volumes created before it keep their accumulated `sessions/` tree,
still reachable by both channels. Nothing writes there any more, which is exactly
why nothing will clean it up. This repair does, once.

**Dry-run by default.** It deletes Agent-authored data that has no other copy
([R12.03a]), so it reports what it would remove and does nothing until `--arm`.
Read a run's output before arming it.

**Fail-open, per volume.** One volume's failure is counted and the sweep
continues: a partial repair is strictly better than none, and the operator needs
the whole picture in one pass rather than one error at a time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.workers.agent_fs_gc import docker_client, parse_agent_id
from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PurgeReport:
    """What one pass saw and did, in volumes."""

    seen: int = 0
    purged: int = 0
    would_purge: int = 0
    failed: int = 0
    dry_run: bool = True


class PurgeUnavailable(RuntimeError):
    """The daemon could not be reached, so nothing could be examined."""


def _enumerate_agent_volumes() -> list[uuid.UUID]:
    """Every `smap-agent-fs-{uuid}` volume on the daemon.

    Reuses `agent_fs_gc`'s parser rather than re-deriving it: that one rejects
    any non-canonical uuid on purpose, because a name we cannot parse belongs to
    something else on the same host and must never be touched. A second, laxer
    parser here would quietly undo that.

    Raises rather than degrading to an empty list, deliberately diverging from
    `agent_fs_gc._enumerate_volumes`. A nightly sweep that skips a bad night is
    fine -- it runs again tomorrow. A one-shot repair reporting "no agent
    volumes" because it could not reach the daemon would tell the operator the
    deployment is clean when nothing was examined at all, and there is no
    tomorrow to correct it.
    """
    try:
        client = docker_client()
        volumes = client.volumes.list()
    except Exception as exc:
        raise PurgeUnavailable(f"could not enumerate Docker volumes: {exc}") from exc
    found: list[uuid.UUID] = []
    for volume in volumes:
        agent_id = parse_agent_id(getattr(volume, "name", "") or "")
        if agent_id is not None:
            found.append(agent_id)
    return sorted(found, key=str)


async def _purge_all(agent_ids: list[uuid.UUID], *, armed: bool, sandbox: Any = None) -> PurgeReport:
    box = sandbox or DockerRunscSandbox()
    purged = failed = would = 0
    for agent_id in agent_ids:
        if not armed:
            would += 1
            _log.warning("purge_session_dirs: DRY RUN would clear sessions/ on %s — pass --arm", agent_id)
            continue
        try:
            await box.purge_legacy_session_dirs(agent_id=agent_id)
        except Exception as exc:
            # Counted, not raised: the remaining volumes are still worth repairing,
            # and an operator needs the full tally from one run.
            failed += 1
            _log.warning("purge_session_dirs: failed on %s: %s", agent_id, exc)
            continue
        purged += 1
        _log.info("purge_session_dirs: cleared sessions/ on %s", agent_id)
    return PurgeReport(
        seen=len(agent_ids),
        purged=purged,
        would_purge=would,
        failed=failed,
        dry_run=not armed,
    )


def run(*, armed: bool = False) -> PurgeReport:
    """Enumerate agent volumes and clear each one's legacy session tree.

    Idempotent: a volume with no `sessions/` reconciles empty to empty. Safe to
    re-run after a partial failure, and cheap once the deployment is clean.
    """
    agent_ids = _enumerate_agent_volumes()
    if not agent_ids:
        _log.info("purge_session_dirs: no agent volumes on this host")
        return PurgeReport(dry_run=not armed)
    return asyncio.run(_purge_all(agent_ids, armed=armed))
