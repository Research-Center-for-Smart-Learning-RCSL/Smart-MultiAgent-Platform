# Phase C — File Search Tool (per-agent, callable)

**Goal.** Expose retrieval over **files a designer uploads for a single agent** as
an agent-callable `file_search` tool — reusing the entire existing RAG pipeline
(ingest → chunk → embed → Qdrant) and the per-document `rag_documents.agent_ids`
allowlist (migration 0035). Additive and **default-off**; the existing always-
inject RAG context path is untouched.

**Size.** M
**Depends on.** A (`hosted_file_search` singleton + `build_agent_tools` dispatch).
**Refs.** `contexts/knowledge/application/{retrieve.py,rag_context_provider.py}`,
`infrastructure/repositories.py::allowed_document_ids`,
`app/api/v1/rag.py`, `app/api/v1/tus.py`,
`frontend/src/slices/agents/views/RagConfigDetailView.vue`.

## C.0 What already exists (reuse, do not rebuild)

- **Ingestion.** `POST /api/rag-configs/{config_id}/documents` (multipart ≤32 MB,
  `agent_ids` form field) and TUS `/api/tus` (`purpose=rag_source`,
  `rag_config_id`, `rag_agent_ids`) → parse → chunk → embed → Qdrant
  `rag_{project_id}` with payload `{doc_id, chunk_idx}`; ClamAV scan; WS progress.
- **Per-agent scoping.** `rag_documents.agent_ids uuid[]` + GIN index;
  `RagDocumentRepository.allowed_document_ids(config_id, agent_id)` returns READY,
  non-quarantined docs where `agent_ids @> [agent_id]`.
- **Retrieval.** `RagContextProvider.query(rag_config_id, query_text, agent_id)`
  → `RetrieveService.query(...)` filters Qdrant by the allowed doc ids and returns
  a `RagContext(block, sources)`.

The only gaps: (1) retrieval is wired as **forced context injection**, not a
callable tool; (2) the upload UI lives under the project RAG config, not the
agent's own Tools surface.

## C.1 Knowledge facade — expose retrieval for tool use — **CODE** — S

**Prerequisite (confirmed gap):** `RagContextProvider.query` currently has **no
`top_k` parameter** (`rag_context_provider.py:58-64`) and does not forward one to
`RetrieveService.query`. First add it:

```python
# rag_context_provider.py
async def query(self, *, rag_config_id, query_text, agent_id=None, top_k=None):
    ...
    return await RetrieveService(self._db).query(..., top_k=top_k)   # already accepts top_k
```

Then add the facade method to `contexts/knowledge/interfaces/facade.py`:

```python
async def search_rag(self, *, rag_config_id, query, agent_id, top_k=None) -> RagContext | None:
    from contexts.knowledge.application.rag_context_provider import RagContextProvider
    return await RagContextProvider(self._db).query(
        rag_config_id=rag_config_id, query_text=query, agent_id=agent_id, top_k=top_k)
```

**SoC note:** a facade lazy-importing its *own* context's application service is
the established pattern here — `AgentsFacade.patch_agent` already lazy-imports
`AgentService` the same way. The DDD rule only forbids reaching into *another*
context's internals, which this does not do.

**Exit criteria.** Facade method returns scoped chunks for an agent that owns a
doc and **nothing** for an agent not on the allowlist (leakage test).

## C.2 `file_search` tool builder — **CODE** — M

Add `_build_file_search_tool` in `contexts/agents/application/runtime/builtin_tools.py`
(referenced by the `HOSTED_FILE_SEARCH` case in A.4):

```python
_FILE_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to search the agent's files for."},
        "top_k": {"type": "integer", "description": "Max passages (1–20)."},
    },
    "required": ["query"],
    "additionalProperties": False,
}

def _build_file_search_tool(db, *, agent, deps, config) -> Tool:
    async def _invoke(args) -> ToolResult:
        if agent.rag_config_id is None:
            return ToolResult(content="file_search unavailable: no knowledge source configured for this agent.", is_error=True)
        top_k = int(args.get("top_k") or config.get("top_k") or 8)
        ctx = await KnowledgeFacade(db).search_rag(
            rag_config_id=agent.rag_config_id, query=str(args.get("query","")),
            agent_id=agent.id, top_k=min(max(top_k,1),20),
        )
        if ctx is None or not ctx.sources:
            return ToolResult(content="No matching passages in the agent's files.")
        return ToolResult(content=_clip(_format_passages(ctx)))   # text + [n] citations
    return Tool(name="file_search",
        description="Search files uploaded for this agent and return relevant passages with citations.",
        input_schema=_FILE_SEARCH_SCHEMA, invoke=_invoke)
```

- `_format_passages` renders each source as `"[n] {title}\n{snippet}"` and a
  trailing citation map, clipped to `_MAX_TOOL_OUTPUT`.
- **Coexistence with context injection.** When both the always-inject RAG path
  (because `rag_config_id` is set) and `file_search` are active, that is allowed —
  injection seeds baseline grounding, the tool lets the model fetch more on
  demand. Document this; do not dedupe across the two paths in v1.

**Exit criteria.** Unit test: enabled `hosted_file_search` with a `rag_config_id`
yields a `file_search` tool that returns scoped passages; with `rag_config_id =
None` the tool returns the structured "unavailable" error (never raises).

## C.3 Enable semantics + validation — **CODE** — S

- In `agent_service`, enabling `hosted_file_search` (via `patch_tool`) requires
  the agent to have a `rag_config_id`. If null, return
  `422 file-search-needs-knowledge-source` (RFC 7807) so the UI can prompt the
  user to attach/create a RAG config first.
- `config.top_k` (optional) is the only writable config key for this singleton;
  clamp 1–20.

**Exit criteria.** Toggling on without a `rag_config_id` is rejected with the
documented problem; with one, it persists.

## C.4 Per-agent upload surface in the Tools UI — **CODE** — M

The **File Search** card (Phase B) gets an expandable panel that lets the designer
manage files **scoped to this agent**, reusing the existing upload machinery — no
new ingestion endpoint.

- **Precondition UI.** If the agent has no `rag_config_id`, the card shows an
  "Attach or create a knowledge source" link to the existing Knowledge tab /
  `RagConfigDetailView`. (A RAG config carries the embedder/model/chunk settings;
  per-agent files are scoped via `agent_ids`, not a separate config.)
- **Upload.** Reuse `RagConfigDetailView`'s upload flow but pre-scope to this one
  agent: call `agentsApi.uploadDocumentMultipart(ragConfigId, file, [agentId])`
  for ≤32 MB, else TUS with `ragAgentIds:[agentId]`. The user is not asked to pick
  agents — the panel is implicitly "this agent only".
- **List.** Show documents whose `agent_ids` contains this agent. Reuse the
  document list component + status/scan badges + `useRagConfigSocket` progress
  channel (`/ws/rag-configs/{config_id}`).
- **Remove from agent.** Reuse `agentsApi.setDocumentAgents(docId, agent_ids)` to
  drop this agent from a doc's allowlist (does not delete the shared doc), and
  `deleteDocument` only when the doc is exclusive to this agent.
- **Backend helper (optional).** If filtering "docs for (config, agent)" client-
  side is awkward, add `GET /api/rag-configs/{config_id}/documents?agent_id=...`
  using `RagDocumentRepository.allowed_document_ids` — small, reuses the repo.

**Exit criteria.** Designer uploads a PDF on the File Search card → progress via
WS → doc shows READY/CLEAN → agent chat: `file_search` returns a passage from it →
removing the agent from the doc hides it from `file_search`.

## C.∞ Phase gate

- [ ] `KnowledgeFacade.search_rag` scoped + leakage-tested.
- [ ] `file_search` tool returns citations; unavailable path is a soft error.
- [ ] Enable requires `rag_config_id`; `top_k` clamped.
- [ ] File Search card uploads/lists/removes per-agent docs reusing RAG endpoints.
- [ ] Existing always-inject RAG behavior unchanged (regression test).
- [ ] `00-overview.md` §0.6: C = done.

## Appendix: Codebase coordinates for implementors

### Backend files to modify

**Knowledge context (3 files):**
- `contexts/knowledge/application/rag_context_provider.py:58-64` — add `top_k: int | None = None` param to `query()` and pass to `RetrieveService` (line ~121)
- `contexts/knowledge/application/retrieve.py` — `RetrieveService.query()` already accepts `top_k` — verify and confirm at the call from rag_context_provider
- `contexts/knowledge/interfaces/facade.py` — add `search_rag()` method; current facade imports only from `.domain` and `.infrastructure`; add lazy import of `RagContextProvider` (matches `AgentsFacade.patch_agent` pattern at `contexts/agents/interfaces/facade.py:67`)

**Agents runtime (1 file):**
- `contexts/agents/application/runtime/builtin_tools.py` — add `_build_file_search_tool` after `_build_file_tool` (line ~223); add `_FILE_SEARCH_SCHEMA`; import `KnowledgeFacade` lazily inside `_invoke`

**Agent service (1 file):**
- `contexts/agents/application/agent_service.py` — in `patch_tool` for `HOSTED_FILE_SEARCH`, validate `rag_config_id is not None` on the agent before enabling

### Existing retrieval flow (verified)

```
RagContextProvider.query(rag_config_id, query_text, agent_id)
  → RetrieveService.query(cfg, query_vec, top_k, doc_ids)
      → self._qdrant.search(project_id, query_vector, top_k, doc_ids)  # scoped by agent's allowed docs
      → optional rerank
      → hydrate chunk text from rag_chunks table
  → return RagContext(block: str, sources: list[dict])
```

Per-agent scoping chain:
1. `RagDocumentRepository.allowed_document_ids(config_id, agent_id)` — `rag_documents WHERE status='ready' AND scan_status != 'quarantined' AND agent_ids @> ARRAY[agent_id]` (GIN index `ix_rag_documents_agent_ids`, migration 0035)
2. Filtered `doc_ids` passed to Qdrant `search()` as payload filter
3. Result: only chunks from documents explicitly shared with this agent

### RAG injection coexistence (verified at turn_engine.py:630-635)

When `agent.rag_config_id` is set, the turn engine **always** calls `_rag_context()` and injects the result into `system_parts`. This path is untouched. `file_search` adds an **on-demand** tool the model can call for deeper retrieval. Both active simultaneously is correct and documented: injection seeds baseline grounding, the tool allows targeted follow-up.

### Frontend reuse (verified)

- Upload: `api/index.ts:199-207` `uploadDocumentMultipart(configId, file, agentIds)` — FormData with `agent_ids` field; TUS for >32MB via `@shared/transport` `tusUpload`
- Document list: `RagConfigDetailView.vue` has status badges, scan status, WS progress (`useRagConfigSocket`)
- Agent scoping on upload: `RagConfigDetailView.vue:88-113` already seeds `uploadAgentIds` with bound agents

## Cross-cutting checklist

1. **AuthZ.** Upload still requires Project Owner (R10.10) — unchanged; the panel
   surfaces the existing endpoint, it does not relax permissions.
2. **Audit.** Reuse `rag.document_uploaded/indexed`; tool calls →
   `mcp.tool_invoked` with `tool="file_search"`.
3. **Tenant isolation.** Qdrant collection stays `rag_{project_id}`; the
   `agent_ids` filter + project-scoped config prevent cross-agent/-tenant leakage.
4. **RFC 7807.** `file-search-needs-knowledge-source`.
