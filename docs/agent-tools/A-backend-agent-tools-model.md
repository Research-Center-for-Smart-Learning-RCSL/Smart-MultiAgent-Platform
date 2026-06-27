# Phase A — Backend `agent_tools` Unification

**Goal.** Replace `agent_mcp_servers` / `McpBinding` / the `source='builtin'`
overload / the `/builtin-tools` gate with a single explicit `agent_tools` model
and a `/api/agents/{id}/tools` surface. No behavior change for existing agents;
this is the structural foundation every later phase builds on.

**Size.** L
**Depends on.** Nothing (foundation).
**Unblocks.** B, C, D, E, F.
**Touched code (all under `backend/`):**

- `alembic/versions/0036_agent_tools.py` (new), `0037_drop_agent_mcp_servers.py` (new)
- `contexts/agents/domain/models.py`, `domain/mcp.py`, `domain/errors.py`
- `contexts/agents/infrastructure/tables.py`, `infrastructure/repositories.py`
- `contexts/agents/application/agent_service.py`, `application/mcp_service.py`,
  `application/runtime/builtin_tools.py`
- `contexts/agents/interfaces/facade.py`
- `app/api/v1/agents.py`, `app/api/v1/mcp.py`
- callers of `list_mcp_bindings` / `build_builtin_tools` (turn engine, workers)

---

## A.1 Migration `0036_agent_tools` — add + backfill — **CODE** — M

Create the enum + table, then backfill from `agent_mcp_servers`. **Add-only** so
old code keeps running on the new schema (N-1).

**DDL** (mirror the style of `0011_agents.py`):

```python
agent_tool_type = sa.Enum(
    "hosted_mcp", "hosted_web_search", "hosted_code_interpreter",
    "hosted_file_workspace", "hosted_file_search",
    "local_function", "local_shell",
    name="agent_tool_type",
)
# op.create table agent_tools per docs/agent-tools/00-overview.md §0.5
#   id, agent_id (fk agents on delete cascade), tool_type, enabled bool default true,
#   display_name text null, config jsonb default '{}', created_at timestamptz default now()
# ix_agent_tools_agent (agent_id)
# uq_agent_tools_singleton partial unique (agent_id, tool_type) WHERE tool_type IN (...4 singletons...)
```

**Backfill** (data migration in the same revision, Python `op.execute` with a
connection loop — do it per agent so the legacy gate is replayed exactly):

1. For every distinct `agent_id` in `agents` (active and soft-deleted — keep the
   GC contract):
   - **Singletons.** Compute the enabled set with the *current* legacy rule
     (replicate `_enabled_builtins`): collect that agent's `agent_mcp_servers`
     rows with `source='builtin'`; if none → all of `{web_search, code_exec,
     file}` enabled; else the union of `reference` and `allowed_tools` entries
     that are in `{web_search, code_exec, file}`. Then insert:
     - `hosted_web_search`     enabled = `web_search ∈ set`
     - `hosted_code_interpreter` enabled = `code_exec ∈ set`
     - `hosted_file_workspace`  enabled = `file ∈ set`
     - `hosted_file_search`     enabled = **false** (new capability, opt-in)
   - **MCP.** For each `source ∈ {url, package}` row, insert one `hosted_mcp` row:
     - `enabled = true`
     - `display_name = reference` (truncated to 200 for readability)
     - `config = { "source": source, "reference": reference,
                   "allowed_tools": allowed_tools, **(old config incl. sealed auth) }`
       i.e. spread the old `config` (which may carry `auth`) and add the three
       moved fields. **Sealed auth is copied verbatim** — same AAD
       (`mcp_binding_auth:<old binding id>`); see A.6 for why the id is preserved.
   - Preserve the original row `id` for MCP rows (`agent_tools.id = old id`) so
     the auth AAD (bound to the binding id) still decrypts. Singletons get fresh ids.

2. **Assertion test** (unit + a migration test): post-backfill, for each agent the
   enabled built-in set derived from `agent_tools` equals `_enabled_builtins(old
   rows)`, and `count(hosted_mcp) == count(old url/package rows)`.

**Down migration.** Drop `agent_tools` + the enum. (The old table still exists at
this revision, so down is clean.)

**Exit criteria.** `alembic upgrade head` then `downgrade -1` round-trips on a DB
seeded with: an agent with no builtin rows (legacy all-on), one with explicit
`code_exec`-only, one with the `__none__` sentinel, one with a url MCP carrying
sealed auth. Backfill assertion test green.

---

## A.2 Domain model — `AgentTool` replaces `McpBinding` — **CODE** — S

**`contexts/agents/domain/models.py`:**

- Add `class AgentToolType(str, enum.Enum)` with the seven values.
- Keep `McpSource` but narrow its role to a **config value validator** for
  `hosted_mcp` (`url`, `package` only — remove `BUILTIN`). Move it to
  `domain/mcp.py` if cleaner; keep an import shim for one release.
- Replace `McpBinding` with:

```python
@dataclass(frozen=True, slots=True)
class AgentTool:
    id: uuid.UUID
    agent_id: uuid.UUID
    tool_type: AgentToolType
    enabled: bool
    display_name: str | None
    config: dict[str, Any]
    created_at: datetime
```

- Add helpers on the dataclass or module level:
  - `SINGLETON_TYPES: frozenset[AgentToolType]` (the 4 hosted singletons).
  - `is_singleton(tool_type) -> bool`.
  - For `hosted_mcp`: `mcp_source(self) -> McpSource`, `mcp_reference(self) -> str`,
    `mcp_allowed_tools(self) -> tuple[str,...]` reading from `config`.

**`domain/errors.py`:** rename `McpBindingNotFound` → `AgentToolNotFound` (keep
`McpBindingNotFound = AgentToolNotFound` alias for one release to avoid churn in
unrelated tests). Add `AgentToolTypeImmutable` (raised when a PATCH/POST tries to
change `tool_type` or create a second singleton).

**Exit criteria.** `mypy .` clean; the dataclass is framework-free (no SQLAlchemy
import) per the DDD rule.

---

## A.3 Table + repository — **CODE** — M

**`infrastructure/tables.py`:** add the `agent_tools` Table object (columns per
§0.5). Keep `agent_mcp_servers` defined until `0037`/A.7 so nothing import-breaks
mid-phase.

**`infrastructure/repositories.py`:** replace `AgentMcpBindingRepository` with
`AgentToolRepository` (keep the old class name as a thin subclass alias for one
release if external tests reference it):

```python
class AgentToolRepository:
    async def list(self, agent_id) -> Sequence[AgentTool]: ...          # order by created_at
    async def list_for_agents(self, agent_ids) -> dict[uuid, list[AgentTool]]: ...  # batch (turn engine)
    async def get(self, *, agent_id, tool_id) -> AgentTool | None: ...
    async def get_singleton(self, *, agent_id, tool_type) -> AgentTool | None: ...
    async def add(self, *, agent_id, tool_type, enabled, display_name, config) -> AgentTool: ...
    async def set_enabled(self, *, agent_id, tool_id, enabled) -> AgentTool: ...
    async def patch(self, *, agent_id, tool_id, enabled=None, display_name=None, config=None) -> AgentTool: ...
    async def remove(self, *, agent_id, tool_id) -> None: ...           # raises AgentToolNotFound
    async def provision_singletons(self, *, agent_id, file_search_enabled=False,
                                    web_search=True, code_interpreter=False,
                                    file_workspace=True) -> None: ...    # idempotent upsert of the 4 rows
```

- **Singletons are never created via `add`.** `add` is for `hosted_mcp` /
  `local_function` only; the service (`add_tool`, A.5) rejects singleton types
  before reaching the repo (`AgentToolTypeImmutable`). The four singleton rows are
  inserted exclusively by `provision_singletons`, which is idempotent via
  `ON CONFLICT (agent_id, tool_type) DO NOTHING` against the partial unique index —
  so there is no "duplicate singleton" path to disambiguate.
- `list_for_agents` (batch) is a single `WHERE agent_id IN (...)` ordered by
  `(agent_id, created_at)`, grouped into `dict[agent_id, list[AgentTool]]` — the
  turn engine uses it to avoid N queries.
- `_row_to_tool(row)` builder mirrors the old `_row_to_binding`.

**Exit criteria.** Repo unit tests: singleton uniqueness enforced; MCP add/list/
patch/remove; `provision_singletons` idempotent.

---

## A.4 Tool assembly — `build_agent_tools` — **CODE** — M

Rewrite `contexts/agents/application/runtime/builtin_tools.py`:

- **Delete** `_enabled_builtins`, `BUILTIN_NONE_SENTINEL`, and the
  `source='builtin'` branch. Enablement is now the explicit `enabled` column.
- Keep `_build_web_search_tool`, `_build_code_exec_tool`, `_build_file_tool`,
  `_build_mcp_tool`, `_clip`, `_audit_mcp_invoke`, `_unseal_binding_auth`
  (rename the last to `_unseal_tool_auth(tool)` reading `tool.config['auth']`).
- New dispatcher:

```python
def build_agent_tools(
    db, *, agent: Agent, tools: list[AgentTool], deps: AgentToolDeps,
    chatroom_id: uuid.UUID | None = None, artifact_sink: list[dict] | None = None,
) -> list[Tool]:
    out: list[Tool] = []
    for t in tools:
        if not t.enabled:
            continue
        match t.tool_type:
            case AgentToolType.HOSTED_WEB_SEARCH:        out.append(_build_web_search_tool(db, agent=agent, deps=deps))
            case AgentToolType.HOSTED_CODE_INTERPRETER:  out.append(_build_code_exec_tool(db, agent=agent, deps=deps, chatroom_id=chatroom_id, artifact_sink=artifact_sink))
            case AgentToolType.HOSTED_FILE_WORKSPACE:    out.append(_build_file_tool(db, agent=agent, deps=deps))
            case AgentToolType.HOSTED_FILE_SEARCH:       out.append(_build_file_search_tool(db, agent=agent, deps=deps, config=t.config))   # Phase C
            case AgentToolType.HOSTED_MCP:
                for name in t.mcp_allowed_tools():
                    out.append(_build_mcp_tool(db, agent=agent, tool=t, mcp_tool=name, deps=deps))
            case AgentToolType.LOCAL_FUNCTION:           out.append(_build_function_tool(db, agent=agent, deps=deps, tool=t))              # Phase E
            case AgentToolType.LOCAL_SHELL:              continue   # not implemented this round (Phase F is FE-only)
    return out
```

- Rename `BuiltinToolDeps` → `AgentToolDeps` and `default_builtin_deps` →
  `default_agent_tool_deps` (keep old names as aliases for one release).
- `_build_mcp_tool` now reads `source`/`reference` from `tool.config` instead of
  the dataclass fields; the namespacing helper keeps `mcp__{tool.id[:8]}__{name}`.

**Exit criteria.** Unit tests: a tool list with each type yields the right
`Tool.name` set; disabled rows are skipped; `local_shell` rows are ignored; MCP
rows expand per allowed tool. A golden test asserts an agent migrated from
"legacy all-on" produces exactly `{web_search, code_exec, file}` (+ any MCP).

---

## A.5 Application service — fold MCP + builtin into one tool surface — **CODE** — M

**`application/agent_service.py`:**

- In `create(...)`, replace the `_DEFAULT_BUILTIN_TOOLS` loop with
  `await self._tools.provision_singletons(agent_id=agent.id,
  web_search=True, code_interpreter=False, file_workspace=True,
  file_search_enabled=False)` (same opt-in defaults: code interpreter OFF).
- Replace `_DEFAULT_BUILTIN_TOOLS` / `_BUILTIN_TOOL_ORDER` / `get_enabled_builtins`
  / `set_builtin_tools` / `add_mcp_binding` / `patch_mcp_binding` /
  `remove_mcp_binding` / `list_mcp_bindings` with a unified tool API:

```python
async def list_tools(self, agent_id) -> list[AgentTool]
async def add_tool(self, *, agent_id, tool_type, display_name, config, auth, actor...) -> AgentTool
async def patch_tool(self, *, agent_id, tool_id, enabled, display_name, config, auth, actor...) -> AgentTool
async def remove_tool(self, *, agent_id, tool_id, actor...) -> None
```

- `add_tool` rules:
  - Reject singleton types (they are auto-provisioned; clients toggle via
    `patch_tool`) → `AgentToolTypeImmutable`.
  - Reject `local_shell` → `422 tool-not-available` (Phase F is FE-only).
  - For `hosted_mcp`: validate `config.source ∈ {url, package}`, non-empty
    `reference`; seal `auth` into `config.auth` after insert (reuse the
    two-stage insert from `mcp_service.add`, generalized in A.6).
  - For `local_function`: validate `config` against the function schema (E.2);
    seal `auth`.
- `patch_tool` rules: `tool_type` is immutable; for singletons only `enabled`
  (+ whitelisted `config` keys like `file_search.top_k`) may change; re-seal
  `auth` if provided.
- **Audit events** (rename, keep stable resource shape): `agent.tool_added`,
  `agent.tool_updated`, `agent.tool_removed`, with metadata
  `{agent_id, tool_type, ...}`. Keep `mcp.tool_invoked` for runtime invocations
  (unchanged) so dashboards survive.

**`application/mcp_service.py`:** keep `McpBindingService.test` (the sandbox
probe) but rename to `AgentToolTestService.test_mcp` and have it load the
`hosted_mcp` row from `AgentToolRepository`, unseal `config.auth`, and probe.
Generalize `_seal_auth`/`unseal_auth` per A.6.

**Exit criteria.** Service unit tests: create agent provisions 4 singleton rows
(web_search+file_workspace enabled, code_interpreter+file_search disabled);
toggling code interpreter flips `enabled`; adding/removing MCP + function rows;
singleton create rejected; `local_shell` create rejected.

---

## A.6 Generalize sealed auth — **CODE** — S

`mcp_service.py::_seal_auth/unseal_auth` bind the envelope AAD to
`b"mcp_binding_auth:" + binding_id`. Generalize without breaking migrated rows.

**Key point: the namespace marker is PLAINTEXT metadata in the sealed dict, NOT
inside the ciphertext.** The sealed dict already carries plaintext fields
(`ciphertext`, `nonce`, `transit_key_version`, …); we add one more plaintext key,
`aad_ns`, read *before* decryption to choose the AAD. This avoids the chicken-and-egg
of needing to decrypt to learn the AAD. The AAD itself is still authenticated by the
envelope, so a tampered marker fails the HMAC.

```python
def seal_tool_auth(tool_id, auth):                      # new tools (MCP + functions)
    rec = env.encrypt_envelope(json.dumps(auth, sort_keys=True).encode(),
                               b"agent_tool_auth:" + str(tool_id).encode("ascii"))
    return {"__sealed__": True, "aad_ns": "agent_tool_auth", **_record_to_json(rec)}

def unseal_tool_auth(tool_id, sealed):
    ns = sealed.get("aad_ns", "mcp_binding_auth")        # absent => legacy namespace
    aad = ns.encode("ascii") + b":" + str(tool_id).encode("ascii")
    return json.loads(env.decrypt_envelope(_json_to_record(sealed), aad).decode())
```

- **Migrated MCP rows keep the old AAD** because A.1 preserved the row `id` and
  copied the old sealed dict verbatim — it has **no** `aad_ns`, so `unseal_tool_auth`
  defaults to `mcp_binding_auth:<id>`, which matches what `_seal_auth` produced.
- New tools (post-migration MCP + all functions) seal with `aad_ns:"agent_tool_auth"`.
- Keep the old `unseal_auth`/`_seal_auth` only until A.9 removes the shims.

**Exit criteria.** Round-trip test: a row sealed with the old namespace (marker
absent) and one with the new namespace both decrypt; flipping `aad_ns` on a sealed
blob makes decryption fail (AAD mismatch).

---

## A.7 API cutover — `/api/agents/{id}/tools` — **CODE / E2E** — M

**`app/api/v1/agents.py`** — replace the MCP + builtin-tools handlers with a
unified tools surface (same AuthZ pattern already in the file:
`RESOURCE_CREATE_EDIT` at `agent.project_id` for mutations, membership for reads):

```
GET    /api/agents/{agent_id}/tools                 -> list[AgentToolOut]
POST   /api/agents/{agent_id}/tools                 -> AgentToolOut   (hosted_mcp | local_function)
PATCH  /api/agents/{agent_id}/tools/{tool_id}       -> AgentToolOut   (enabled / config / display_name / auth)
DELETE /api/agents/{agent_id}/tools/{tool_id}       -> 204            (mcp / function only)
POST   /api/agents/{agent_id}/tools/{tool_id}/test  -> ToolTestOut    (mcp probe now; function ping in E)
```

**Pydantic models:**

```python
class AgentToolOut(BaseModel):
    id: uuid.UUID; agent_id: uuid.UUID
    tool_type: str; enabled: bool
    display_name: str | None
    config: dict[str, Any]          # sealed auth REDACTED -> see below
    created_at: str

class AgentToolCreateIn(BaseModel):
    tool_type: Literal["hosted_mcp","local_function"]
    display_name: str | None = Field(default=None, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    auth: dict[str, Any] | None = None     # plaintext in -> sealed at rest, never returned

class AgentToolPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool | None = None
    display_name: str | None = Field(default=None, max_length=200)
    config: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
```

- **Redaction (fixes a real existing leak).** Today `McpBindingOut.config` returns
  the verbatim `config`, which includes the sealed `auth` envelope
  (`app/api/v1/agents.py:153,187`). `_to_tool_out` must strip it:

  ```python
  def _to_tool_out(t: AgentTool) -> AgentToolOut:
      cfg = {k: v for k, v in t.config.items() if k != "auth"}
      if "auth" in t.config:
          cfg["auth_present"] = True
      return AgentToolOut(id=t.id, agent_id=t.agent_id, tool_type=t.tool_type.value,
                          enabled=t.enabled, display_name=t.display_name,
                          config=cfg, created_at=t.created_at.isoformat())
  ```
  A security test asserts no response body ever contains `ciphertext`/`dek_wrapped`.
- **Validation parity** with the old `add_mcp_binding` builtin guard is gone (no
  more builtin bindings); instead validate `hosted_mcp.config.source/reference`
  and `local_function` config (E.2) server-side.
- **Keep the egress-allowlist router** in `app/api/v1/mcp.py` unchanged
  (`/api/projects/{pid}/mcp/egress-allowlist`); only repoint the `/test` handler
  to the renamed test service. (Optional later rename to
  `…/egress-allowlist` under a project-egress path — out of scope.)

**Deprecation window.** Land `/tools` alongside the old `/mcp` + `/builtin-tools`
handlers, both operating on `agent_tools` (the old handlers become thin adapters:
`/builtin-tools` GET/PUT maps the 3 names to the singleton `enabled` flags; `/mcp`
maps to `hosted_mcp` rows). Remove the old handlers only after **B** is E2E-green.

**Facade.** Add `list_agent_tools(agent_id) -> list[AgentTool]` to
`interfaces/facade.py` and export `AgentTool` in `__all__`. **Do not** keep a
`list_mcp_bindings` alias that returns `list[AgentTool]` under a `McpBinding`-typed
signature — that is a type lie that breaks `mypy` at the call sites. Instead, the
single in-tree caller (the turn engine, A.8) is migrated to `list_agent_tools` +
`build_agent_tools` **in this same phase**, so no compatibility alias is needed.
If an out-of-tree consumer truly needs a short shim, it returns `list[AgentTool]`
filtered to `HOSTED_MCP` and is removed in A.9 — never typed as `McpBinding`.

**Exit criteria.** Contract: OpenAPI shows the five `/tools` routes; sealed auth
never appears in any response (assert in a security test). E2E: create agent →
GET tools shows 4 singletons → toggle code interpreter → add a url MCP with auth
→ GET shows `auth_present:true`, no ciphertext → delete MCP.

---

## A.8 Update internal callers — **CODE** — S

Repoint everything that consumed the old surface:

- `turn_engine.py` (`_builtin_tools` / wherever `list_mcp_bindings` +
  `build_builtin_tools` are called) → `facade.list_agent_tools` +
  `build_agent_tools`. The `_stage_workspace_inputs` gate that checks
  `"code_exec" in _enabled_builtins(...)` becomes
  `any(t.enabled and t.tool_type == HOSTED_CODE_INTERPRETER for t in tools)`.
- Any worker / orchestration code importing `McpBinding`, `BuiltinToolDeps`,
  `build_builtin_tools`, `_enabled_builtins` → new names (aliases cover the gap,
  but update them to drop the aliases in `0037`'s cleanup).
- `frontend`-facing generated client is handled in B.

**Exit criteria.** `grep -r "agent_mcp_servers\|McpBinding\|_enabled_builtins\|
build_builtin_tools\|builtin-tools" backend/` returns only the deprecation shims
+ tests; full backend unit + wiring tiers green.

---

## A.9 Migration `0037_drop_agent_mcp_servers` — **CODE** — S

After A.7 cutover + B are merged and green: drop `agent_mcp_servers` and the
`agent_mcp_source` enum, and delete the deprecation aliases/shims (old endpoint
handlers, `McpBinding` alias, `list_mcp_bindings` alias, `BuiltinToolDeps` alias).

**Exit criteria.** `alembic upgrade head` clean on a DB already at `0036`; no code
references the dropped table/enum; CI green.

---

## A.∞ Phase gate

- [ ] `0036` add+backfill round-trips; backfill assertion test green for legacy-
      all-on, explicit-subset, `__none__`, and url-MCP-with-auth agents.
- [ ] `AgentTool` domain type is framework-free; `mypy` clean.
- [ ] `build_agent_tools` produces correct tool sets per type; disabled + shell
      skipped.
- [ ] Agent create provisions 4 singletons with the opt-in defaults.
- [ ] `/api/agents/{id}/tools` CRUD + `/test` live; sealed auth never serialized.
- [ ] All internal callers migrated; deprecation shims isolated.
- [ ] `0037` drops the old table after B is green; no dangling references.
- [ ] `00-overview.md` §0.6: A = done.

## Appendix: Codebase coordinates for implementors

### Files to modify (exhaustive, 19 files + 2 new migrations)

**Domain (3 files):**
- `contexts/agents/domain/models.py:28-31` — `McpSource` enum (narrow to url/package or deprecate BUILTIN)
- `contexts/agents/domain/models.py:57-65` — `McpBinding` dataclass → `AgentTool`
- `contexts/agents/domain/mcp.py:15,18-31` — `McpServerDraft` (keep for sealed auth reuse or fold into tool service)
- `contexts/agents/domain/errors.py:56-57` — `McpBindingNotFound` → `AgentToolNotFound` + add `AgentToolTypeImmutable`; update `__all__` (line 109-127)

**Infrastructure (2 files):**
- `contexts/agents/infrastructure/tables.py:63-79` — add `agent_tools` Table; keep `agent_mcp_servers` until 0037
- `contexts/agents/infrastructure/repositories.py:254-351` — `AgentMcpBindingRepository` → `AgentToolRepository`; `_row_to_binding` (line 60-69) → `_row_to_tool`

**Application (4 files):**
- `contexts/agents/application/agent_service.py` — replace MCP methods (lines 343-515); replace default-builtin creation (lines 168-175); rename `_bindings` (line 72) → `_tools`
- `contexts/agents/application/mcp_service.py` — rename class (line 72), generalize sealed auth (lines 37-69), keep `test()` method, update `agent_mcp_servers` direct reference (line 110-112)
- `contexts/agents/application/runtime/builtin_tools.py` — `BuiltinToolDeps` (line 39) → `AgentToolDeps`; `build_builtin_tools` (line 348) → `build_agent_tools`; delete `_enabled_builtins` (line 326), `BUILTIN_NONE_SENTINEL` (line 323), remove `McpSource.BUILTIN` branch (line 337,381); keep tool builders (lines 115-264) and `_unseal_binding_auth` (line 293) renamed
- `contexts/agents/application/runtime/tool_registry.py` — no structural change; `build_registry` (line 220) passes `extra` tools, stays as-is

**Interfaces (2 files):**
- `contexts/agents/interfaces/facade.py:15-17,31,46-47` — add `list_agent_tools`; export `AgentTool` in `__all__` (line 24)
- `contexts/agents/interfaces/error_mapping.py:42-46` — rename `McpBindingNotFound` entry to `AgentToolNotFound`; add `AgentToolTypeImmutable → 409`

**API routes (2 files):**
- `app/api/v1/agents.py` — replace `McpBindingCreateIn/PatchIn/Out` (lines 134-154), `_to_binding_out` (line 180-189), the 6 MCP+builtin handlers (lines 404-613); add `/tools` handlers
- `app/api/v1/mcp.py:162-203` — repoint test handler from `McpBindingService` to renamed service loading `AgentTool`

**Router registration:**
- `app/api/v1/__init__.py` — import new route module (or reuse agents.py); pattern at line 42,80 imports module, line 163-175 returns `RouterEntry` objects

**Turn engine (critical caller):**
- `contexts/agents/application/runtime/turn_engine.py:334-337` — `build_builtin_tools` + `default_builtin_deps` import → `build_agent_tools` + `default_agent_tool_deps`
- `turn_engine.py:339` — `AgentsFacade.list_mcp_bindings(agent.id)` → `list_agent_tools(agent.id)`
- `turn_engine.py:340-347` — `build_builtin_tools(...)` → `build_agent_tools(..., tools=list(tools), ...)`
- `turn_engine.py:361` — `_enabled_builtins` import → check `any(t.enabled and t.tool_type == HOSTED_CODE_INTERPRETER for t in tools)`
- `turn_engine.py:368-369` — same pattern as 361

**Audit event strings to rename (5):**
- `agent_service.py:370` `"agent.mcp_binding_added"` → `"agent.tool_added"`
- `agent_service.py:412` `"agent.mcp_binding_updated"` → `"agent.tool_updated"`
- `agent_service.py:440` `"agent.mcp_binding_removed"` → `"agent.tool_removed"`
- `mcp_service.py:126` `"mcp.binding_created"` → merge into `"agent.tool_added"`
- `mcp_service.py:156` `"mcp.binding_deleted"` → merge into `"agent.tool_removed"`

**Test files (3):**
- `tests/unit/test_agent_service.py` — `McpBinding`/`McpSource` imports (lines 29-30); `TestMcpBindings` class (line 490); default builtin assertions (line 134)
- `tests/unit/test_mcp_service.py` — `McpBindingService` (line 11); `_FakeBindingRepo` (line 43); all test methods
- `tests/unit/test_builtin_tools_wiring.py` — `bt.BuiltinToolDeps` (line 36); `bt.build_builtin_tools` (59+ calls); `McpSource.PACKAGE` (line 29)

### Migration patterns to follow

- **Enum creation:** `op.execute("CREATE TYPE agent_tool_type AS ENUM (...)")` — see `0011_agents.py:35-54`
- **Data backfill:** `op.execute("""INSERT INTO ... SELECT ...""")` for bulk; Python loop via `op.get_bind().execute()` for per-agent logic — see `0035_rag_document_agent_scope.py:49-57`
- **Partial unique index:** `op.execute("CREATE UNIQUE INDEX uq_agent_tools_singleton ON agent_tools (agent_id, tool_type) WHERE tool_type IN (...)")` — see `0011_agents.py:102-105`
- **Down migration:** drop in reverse order (index → table → enum) — see `0011_agents.py:128-137`
- **Revision chain:** `down_revision = "0035_rag_document_agent_scope"` for 0036
- **No version-bump trigger needed:** `agent_tools` has no `version` column (agents' own version covers optimistic locking); the `smap_bump_version` trigger (migration 0029) only applies to tables with a `version` column

### Error registration pattern

```python
# errors.py — add:
class AgentToolNotFound(AgentsError):
    code = "agents/tool-not-found"
class AgentToolTypeImmutable(AgentsError):
    code = "agents/tool-type-immutable"

# error_mapping.py — add to _MAP:
errors.AgentToolNotFound: ("agents/tool-not-found", 404, "Agent tool not found"),
errors.AgentToolTypeImmutable: ("agents/tool-type-immutable", 409, "Cannot create a second singleton tool of this type"),
```

## Cross-cutting checklist

1. **AuthZ.** `RESOURCE_CREATE_EDIT` at `agent.project_id` for mutations;
   membership for reads (identical to current handlers).
2. **Audit.** `agent.tool_added/updated/removed`; `mcp.tool_invoked` unchanged.
3. **Rate limit.** Tool CRUD joins the existing resource-edit bucket; no new bucket.
4. **Observability.** Add `agent_tools_total{tool_type,enabled}` gauge sampled by
   the existing metrics job (optional, low priority).
5. **RFC 7807.** `tool-not-available` (local_shell create), reuse
   `mcp-timeout` / `mcp-egress-denied` for the probe.
6. **Migration policy.** `0036` add-only (N-1 safe); `0037` drop after cutover.
7. **Secrets.** `config.auth` sealed via Vault Transit; redacted on read.
