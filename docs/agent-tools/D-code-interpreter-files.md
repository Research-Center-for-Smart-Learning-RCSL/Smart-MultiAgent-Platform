# Phase D — Code Interpreter Designer Uploads

**Goal.** Let an agent designer upload files **for a single agent** that land,
byte-for-byte, in the agent's Code-Interpreter `/workspace` so `code_exec` can
`open()` them (and the `file` workspace tool can list/read them). **MinIO is the
source of truth**; files are hydrated into the per-agent volume on use, reusing
the existing `stage_kernel_inputs` archive path.

**Size.** M
**Depends on.** A (`hosted_code_interpreter` singleton).
**Refs.** `contexts/agents/infrastructure/sandbox/docker_runsc.py`
(`stage_kernel_inputs`, `_tar_staged_inputs`, the `smap-agent-fs-{agent_id}`
volume), `contexts/agents/application/runtime/turn_engine.py::_stage_workspace_inputs`,
`contexts/agents/domain/mcp.py::StagedFile`,
`contexts/knowledge/infrastructure/blob_store.py`, `app/config/settings.py::MinioSection`,
`app/workers/agent_fs_gc.py`, `smap/bootstrap` minio init.

## D.0 What already exists (reuse)

- `StagedFile(filename, data)` + `docker_runsc.stage_kernel_inputs(agent_id,
  chatroom_id, files)` already tar-stream files into
  `/workspace/sessions/{room}/inputs/` on the per-agent volume via a no-network
  helper container's `put_archive` — **the exact mechanism we need**.
- `turn_engine._stage_workspace_inputs` already converts the triggering chat
  message's attachments to `StagedFile`s and stages them (gated on `code_exec`
  enabled, caps 10 files / 64 MB).
- `MinioBlobStore.put/get(bucket, key, ...)`; buckets configured in `MinioSection`
  and provisioned by `smap/bootstrap` minio init.
- `agent_fs_gc.py` nightly purges `smap-agent-fs-{agent_id}` volumes 60 days after
  soft-delete.

Gaps: (1) no durable per-agent file store (MinIO) separate from chat attachments;
(2) no metadata table; (3) no hydration of *persisted* agent files (vs. one
message's attachments) into a stable workspace dir.

## D.1 Migration `0038_agent_workspace_files` — **CODE** — S

```
agent_workspace_files (
  id          uuid pk default gen_random_uuid(),
  agent_id    uuid not null references agents(id) on delete cascade,
  path        text not null,            -- workspace-relative, e.g. "data/sales.csv"
  size_bytes  bigint not null,
  sha256      char(64) not null,
  mime        text not null,
  minio_key   text not null,            -- object key within the workspace bucket
  created_by  uuid references users(id) on delete set null,
  created_at  timestamptz not null default now(),
  UNIQUE (agent_id, path)               -- one logical path per agent
);
CREATE INDEX ix_agent_workspace_files_agent ON agent_workspace_files (agent_id);
```

**Exit criteria.** Up/down round-trips; unique `(agent_id, path)` enforced.

## D.2 MinIO bucket + storage layout — **CODE / OPS** — S

- Add `bucket_agent_workspace: str = "agent-workspace"` to `MinioSection`
  (`SMAP_MINIO_BUCKET_AGENT_WORKSPACE`).
- Provision it in `smap/bootstrap` minio init alongside `rag-sources` etc.
- Object key: `agent-workspace/{agent_id}/{sha256}` (content-addressed; dedups
  identical bytes). The logical `path` lives in the metadata row, not the key, so
  renames don't move blobs.

**Exit criteria.** Bootstrap creates the bucket; OPS note in `deploy/README`.

## D.3 Upload / list / delete API — **CODE** — M

New routes in a small router (e.g. `app/api/v1/agent_workspace.py`), AuthZ
`RESOURCE_CREATE_EDIT` at `agent.project_id` (the agent designer):

```
POST   /api/agents/{agent_id}/workspace-files            (multipart ≤32 MB)  -> WorkspaceFileOut
GET    /api/agents/{agent_id}/workspace-files                                -> list[WorkspaceFileOut]
DELETE /api/agents/{agent_id}/workspace-files/{file_id}                      -> 204
```

- **TUS** for larger files: extend `app/api/v1/tus.py` with `purpose=agent_workspace`
  + `agent_id` metadata; the finalizer streams to the workspace bucket and inserts
  the metadata row (mirror `RagTusFinalizer`).
- Multipart handler: read bytes (cap 32 MB), compute sha256, validate `path`
  (reuse the sandbox path guard — no `..`, no absolute, no symlink components;
  default `path = sanitized filename` under a `data/` prefix), `blob.put(bucket,
  key, data, mime)`, upsert the metadata row (replace on `(agent_id, path)`).
- `WorkspaceFileOut`: `{id, agent_id, path, size_bytes, mime, created_at}`.
- Per-agent quota: add `agent_workspace_quota_bytes: int = 256*1024*1024` to a
  settings section (alongside `MinioSection`, or a new `AgentSection`); enforce by
  summing `size_bytes` for the agent before insert → `413 workspace-quota-exceeded`.
- Delete: remove the metadata row; the key is `agent-workspace/{agent_id}/{sha256}`
  (dedup is **per-agent**, not global), so delete the MinIO object only if no other
  row **of the same agent** references that `sha256` —
  `SELECT count(*) FROM agent_workspace_files WHERE agent_id=? AND sha256=?`.

**Exit criteria.** Upload → list shows it; second upload to the same `path`
replaces; quota breach returns the problem; delete removes row (+ orphan blob).

## D.4 Hydration into `/workspace` — **CODE** — M

Add a sibling to `stage_kernel_inputs` in `docker_runsc.py`:

```python
async def stage_agent_workspace_files(self, *, agent_id, files: Sequence[StagedFile],
                                      manifest_sha: str) -> list[str]:
    """Materialize the agent's persisted files under /workspace/agent-files/.
    Idempotent: writes a marker /workspace/.agent-files-manifest; if it already
    equals manifest_sha, skip the copy (no container spawn)."""
```

- Implementation mirrors `stage_kernel_inputs`: tar files into a stable dir
  `agent-files/` (not the per-room `sessions/{room}/inputs/`), `put_archive` into
  `/workspace` via a no-network helper container. After writing, also write the
  marker file with `manifest_sha`.
- **Skip-when-current (avoid a per-turn container spawn).** `manifest_sha =
  sha256(sorted "path:sha256" pairs)` for the agent, computed cheaply from the
  `agent_workspace_files` rows (no Docker). Keep a **module-level in-memory map**
  `{(agent_id, kernel_session): last_hydrated_sha}` next to the `_KERNELS` registry;
  if it already equals `manifest_sha`, return immediately — **no `run_file_op`, no
  container**. The `/workspace/.agent-files-manifest` marker file is only the
  cold-start fallback (process restart / new kernel) — read it once on a cache miss,
  not every turn. After a successful copy, update both the in-memory map and the
  marker. (Reading a 64-byte marker via `run_file_op` spawns a full container, so it
  must not be on the hot path.)
- **Wire into the turn.** In `turn_engine._stage_workspace_inputs` (already gated on
  code interpreter enabled): stage the agent's persisted files **first**, then the
  triggering message's attachments. Give each its **own** byte budget — keep the
  existing `_MAX_STAGED_BYTES = 64 MB` for attachments and add a separate
  `_MAX_AGENT_FILES_BYTES` (e.g. 128 MB) for persisted files — so a large attachment
  can't starve the agent's own data files (and vice-versa). Fetch bytes from MinIO
  concurrently, wrap as `StagedFile`, call `stage_agent_workspace_files`, and fold
  the returned paths into the same "[Files available in the code_exec workspace: …]"
  system note. Best-effort: a hydration fault must never abort the turn (same
  `try/except` as the existing method).
- **Persistent-kernel note.** Because the volume persists across turns and the
  kernel session reuses it, hydration runs at most once per `manifest_sha`; a
  designer adding a file bumps the manifest and the next turn re-hydrates.

**Exit criteria.** Designer uploads `data.csv`; a chat turn runs `code_exec` doing
`open('agent-files/data.csv')` and reads it; uploading a second file re-hydrates;
an unchanged manifest spawns **no** copy container (assert via a runner spy).

## D.5 Frontend — uploads under the Code Interpreter card — **CODE** — M

In `AgentToolsView` (Phase B), the **Code Interpreter** card gets an expandable
"Files" panel:

- Visible only when `hosted_code_interpreter.enabled`.
- Upload (drag/drop + picker) → `agentsApi.uploadWorkspaceFileMultipart(agentId,
  file)` (≤32 MB) or TUS `purpose=agent_workspace`. Show progress.
- List with `path`, size, delete. Note text: "These files are available to this
  agent's Code Interpreter and File workspace tools at `agent-files/<path>`."
- New `api/index.ts` methods + a `WorkspaceFile` type; new
  `agentKeys.workspaceFiles(agentId)` query invalidated on mutation.
- i18n under `agents.tools.codeInterpreter.files.*` (en + zh-TW).

**Exit criteria.** Upload/list/delete works; the path hint matches what the tool
sees; toggling Code Interpreter off hides the panel.

## D.6 Retention / cleanup — **CODE** — S

- `agent_fs_gc.py::run_once` today only removes Docker volumes (it has no MinIO
  client). Extend it: for each purgeable agent id, build a MinIO client
  (`shared_kernel.storage.get_minio_client`), list and delete the whole
  `agent-workspace/{agent_id}/` prefix, then let the FK cascade drop the
  `agent_workspace_files` rows. Because dedup is **per-agent**, deleting the entire
  agent prefix needs **no** cross-agent orphan check — every object under it belongs
  to that one agent.
- Interactive single-file delete (D.3) keeps the per-agent `sha256` orphan check;
  bulk agent deletion here drops the prefix wholesale.

**Exit criteria.** Deleting an agent (post-retention) removes its workspace
objects; no orphaned MinIO objects remain.

## D.∞ Phase gate

- [ ] `0038` table + unique `(agent_id, path)`.
- [ ] `agent-workspace` bucket provisioned; content-addressed keys.
- [ ] Upload/list/delete API + TUS path; quota enforced.
- [ ] `stage_agent_workspace_files` hydrates `agent-files/`; manifest skip works;
      wired into the turn; faults never abort.
- [ ] FE Files panel under Code Interpreter; path hint accurate.
- [ ] Retention purges MinIO + rows on agent deletion.
- [ ] `00-overview.md` §0.6: D = done.

## Appendix: Codebase coordinates for implementors

### Existing staging machinery (verified, fully reusable)

**StagedFile domain type:**
- `contexts/agents/domain/mcp.py:80-84` — `StagedFile(filename: str, data: bytes)`

**Tar archive builder:**
- `contexts/agents/infrastructure/sandbox/docker_runsc.py:108-141` — `_tar_staged_inputs(rel_dir, files)` → `(archive_bytes, list[staged_relative_paths])`; creates directories with `uid=gid=10001`, files `mode=0o600`; handles filename collisions with `-N` suffix

**stage_kernel_inputs (the pattern to mirror):**
- `docker_runsc.py:852-890` — creates a no-network helper container with the per-agent volume (`smap-agent-fs-{agent_id}`) mounted at `/workspace`, calls `container.put_archive("/workspace", archive)`, returns `list[str]` of staged paths
- Uses `self.code_exec_image` for the helper container
- Runs under `_get_semaphore()` (concurrency cap)

**Per-agent volume mount pattern:**
- `docker_runsc.py:495-499` (file ops): `volume = f"smap-agent-fs-{agent_id}"`, `host_config["volumes"] = {volume: {"bind": "/workspace", "mode": "rw"}}`
- `docker_runsc.py:793-815` (kernel creation): same volume, same mount

**Turn engine staging call site:**
- `turn_engine.py:352-402` — `_stage_workspace_inputs()` method; lazy imports `_enabled_builtins`, `StagedFile`, `docker_runsc_sandbox_from_settings`, `ConversationFacade`
- Budget caps at `turn_engine.py:71-74`: `_MAX_STAGED_FILES = 10`, `_MAX_STAGED_BYTES = 64 * 1024 * 1024`
- Call at `turn_engine.py:648-652`: result folded into `system_parts` before provider call

**MinIO / BlobStore:**
- Protocol: `contexts/knowledge/application/ports.py:24-38` — `BlobStore.put(bucket, key, data, content_type)` / `.get(bucket, key)`
- Implementation: `contexts/knowledge/infrastructure/blob_store.py:22-55` — `MinioBlobStore` wrapping `Minio` client via `asyncio.to_thread`
- Client factory: `shared_kernel/storage/__init__.py` — `get_minio_client()` from settings
- Settings: `app/config/settings.py:76-95` — `MinioSection` with `bucket_chat_uploads`, `bucket_rag_sources`, `bucket_exports`; env prefix `SMAP_MINIO_`

**GC worker:**
- `app/workers/agent_fs_gc.py:1-115` — `run_once()` finds purgeable agent_ids (soft-deleted >60 days), removes Docker volumes only; needs extension for MinIO sweep

**TUS purpose extension pattern:**
- `app/api/v1/tus.py:94-148` — `purpose` metadata selects finalizer; currently handles `rag_source` with `rag_config_id` + `rag_agent_ids` metadata
- Add `purpose=agent_workspace` with `agent_id` metadata; route to a new `WorkspaceTusFinalizer`

### Kernel session registry (for manifest caching)

- `docker_runsc.py:198-214` — `_KERNELS: dict[str, _KernelHandle] = {}` (module-global); `_KernelHandle(container_id, last_used, lock)`; max 16 live, idle reap 900s
- `_session_key(agent_id, chatroom_id) -> str` at line ~185: the kernel dict key
- **Manifest cache should sit alongside**: `_WORKSPACE_MANIFESTS: dict[uuid.UUID, str] = {}` keyed by `agent_id` (not per-session — persistent files are the same across all sessions for an agent)

## Cross-cutting checklist

1. **AuthZ.** Upload/delete require `RESOURCE_CREATE_EDIT` at the agent's project.
2. **Audit.** `agent.workspace_file_added/removed`.
3. **Path safety.** Reuse the sandbox path guard (no `..`/absolute/symlink); files
   land only under `/workspace/agent-files/`.
4. **Resource caps.** 32 MB/file multipart, TUS for larger; 256 MB/agent quota;
   64 MB hydration budget per turn (shared with attachment staging).
5. **Secrets.** No secrets in workspace files; standard MinIO server-side storage.
