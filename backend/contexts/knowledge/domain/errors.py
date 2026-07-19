"""Knowledge (RAG) domain errors → RFC 7807 slugs."""

from __future__ import annotations


class KnowledgeError(Exception):
    code: str = "knowledge/generic"


class RagConfigNotFound(KnowledgeError):
    code = "knowledge/rag-config-not-found"


class RagConfigNameTaken(KnowledgeError):
    code = "knowledge/rag-config-name-taken"


class RagDocumentNotFound(KnowledgeError):
    code = "knowledge/rag-document-not-found"


class UnsupportedMime(KnowledgeError):
    """R10.03 — we support pdf / docx / md / txt. No HTML."""

    code = "knowledge/unsupported-mime"


class EmbedModelNotWhitelisted(KnowledgeError):
    """R10.05."""

    code = "knowledge/embed-model-not-whitelisted"


class CapabilityMismatch(KnowledgeError):
    """Attached `api_key` lacks the required capability (embedding / rerank)."""

    code = "capability-mismatch"


class EmbedDimensionConflict(KnowledgeError):
    """A project's RAG configs must all embed at the same vector dimension.

    They share one Qdrant collection sized to the first config's dimension, so a
    sibling config on a different-dimension model would fail every upsert."""

    code = "knowledge/embed-dimension-conflict"


class RagCollectionDimensionMismatch(KnowledgeError):
    """F-11 (Q-3) — File RAG runtime guard: the ``rag_{project_id}`` collection
    already exists at a different vector dimension than the embedder now produces.

    The File RAG counterpart of :class:`GraphRagCollectionDimensionMismatch`:
    raised by ``QdrantStore.ensure_collection`` so a wrong-size upsert fails as a
    clean typed error at the boundary instead of an opaque raw Qdrant rejection.
    Raised in the ingest path (no direct HTTP surface); mapped defensively as a
    500."""

    code = "knowledge/rag-collection-dimension-mismatch"


class DocumentTooLarge(KnowledgeError):
    code = "knowledge/document-too-large"


class IngestFailed(KnowledgeError):
    code = "knowledge/ingest-failed"


class ChunkParamsInvalid(KnowledgeError):
    code = "knowledge/chunk-params-invalid"


class ChunkParamsImmutable(KnowledgeError):
    """F-20 (R10.04) — chunk parameters are fixed once the config has any document.

    Chunk params (``chunk_size_tokens``/``chunk_overlap_tokens`` for fixed,
    ``similarity_threshold`` for semantic) describe the whole corpus, but chunking
    is a live read of the config at each ingest with no per-document provenance, so
    a change after documents exist would leave the corpus split between two chunking
    policies. Like ``chunk_strategy`` and the embedding model (already immutable), a
    *changing* patch is rejected (409) once a locking document (``INGESTING``/
    ``READY``) exists; while the config is empty the params stay editable."""

    code = "knowledge/chunk-params-immutable"


class GraphRagConfigNotFound(KnowledgeError):
    code = "knowledge/graphrag-config-not-found"


class GraphRagBuildBusy(KnowledgeError):
    """R11a.01 — another build holds the Redis lock for this config."""

    code = "knowledge/graphrag-build-busy"


class GraphRagBuildFailed(KnowledgeError):
    """Builder entered a terminal `failed` state during the current run."""

    code = "knowledge/graphrag-build-failed"


class GraphRagResetCompensationFailed(KnowledgeError):
    """F-26 — an admin reset (``force=false``) could not compensate the in-flight
    build's external state (Neo4j rollback failed / a store was unreachable — the
    "reconciliation is stuck" case R11a.02 names).

    The reset refuses rather than advertise an inconsistent config as healthy
    (R11.04): the config keeps its recovery material (snapshot + current pointer)
    and stays in its prior state. Mapped 503. An explicit ``force=true`` overrides
    this and forces ``idle`` with a recorded, non-null ``last_build_error``.

    Two distinct causes share this error, told apart by the audit ``outcome``:
    ``compensation_failed`` is transient and worth retrying (the config is still
    reconciler-visible, so the sweep may heal it first), while
    ``compensation_unavailable`` means the recovery material expired and no retry
    can ever succeed — only ``force=true`` or a rebuild resolves that one."""

    code = "knowledge/graphrag-reset-compensation-failed"


class KnowmapResetCompensationFailed(KnowledgeError):
    """The Knowledge Map counterpart of :class:`GraphRagResetCompensationFailed`.

    Identical semantics and 503 mapping — both products bind the same
    ``perform_admin_reset`` — but a distinct code so an API consumer is not told a
    knowmap config failed with a ``graphrag-`` error, matching how
    ``KnowmapConfigNotFound`` parallels ``GraphRagConfigNotFound``."""

    code = "knowledge/knowmap-reset-compensation-failed"


class GraphRagConfigAlreadyExists(KnowledgeError):
    """R11.05 — a GraphRAG config is 1:1 with its agent; a second create is a 409."""

    code = "knowledge/graphrag-config-already-exists"


class GraphRagAgentProjectMismatch(KnowledgeError):
    """Agent referenced in the draft does not belong to the target project."""

    code = "knowledge/graphrag-agent-project-mismatch"


class GraphRagBuilderKeyGroupProjectMismatch(KnowledgeError):
    """Builder key group does not belong to the target project."""

    code = "knowledge/graphrag-builder-key-group-project-mismatch"


class GraphRagEmbedDimensionConflict(KnowledgeError):
    """Phase 2a D2 — every GraphRAG config in a project must embed at one dimension.

    All of a project's configs share one ``graphrag_{project_id}`` Qdrant
    collection whose vector size is fixed at first build. A config whose builder
    key group resolves to a different-dimension embedding model is rejected at
    create/update rather than silently mis-indexing (R11.18)."""

    code = "knowledge/graphrag-embed-dimension-conflict"


class GraphRagEmbeddingModelChangeBlocked(KnowledgeError):
    """F-13 (R11.19) — a Concept Map's builder Key Group cannot be swapped to a
    different embedding provider/model while the config holds indexed vectors.

    A same-dimension model change produces a semantically incompatible vector
    space (cosine similarity across two models is meaningless) and defeats the
    "single embedding model" half of the [R11.19] invariant just as surely as a
    dimension change. Rejected at update (409) once the config has entered at
    least one build (``last_build_at IS NOT NULL``, fail-closed); the designer
    clears/recreates to change the model. A never-built config may change freely
    (no vectors to invalidate)."""

    code = "knowledge/graphrag-embedding-model-change-blocked"


class GraphRagOwnerProjectMismatch(KnowledgeError):
    """Phase 2a D6 — the config's owner entity is not in the config's project.

    Enforced for every ``owner_kind`` (agent_group / chatroom / workspace) so a
    project's builder can never be pointed at an owner in another tenant
    (multi-tenant AuthZ, blueprint AC-9a)."""

    code = "knowledge/graphrag-owner-project-mismatch"


class GraphRagCollectionDimensionMismatch(KnowledgeError):
    """Phase 2a D7 — a build produced vectors whose dimension differs from the
    project's existing Qdrant collection.

    The build-time backstop to D2's create/update pin guard: even if a config
    slips through with a drifted dimension, the collection is never mis-indexed —
    the build fails loudly here instead. Raised in the worker build path, so it
    has no HTTP surface, but is mapped defensively as a 500."""

    code = "knowledge/graphrag-collection-dimension-mismatch"


class GraphRagInvalidHalfLife(KnowledgeError):
    """Phase 2b WS5 (R11.21) — recency_half_life_days must be a positive number.

    A zero/negative half-life has no meaningful decay curve; NULL is allowed and
    means "inherit the platform default". Validated on create/update (422)."""

    code = "knowledge/graphrag-invalid-half-life"


# ---- Knowledge Map (Phase 3) ------------------------------------------------


class KnowmapConfigNotFound(KnowledgeError):
    code = "knowledge/knowmap-config-not-found"


class KnowmapConfigNameTaken(KnowledgeError):
    code = "knowledge/knowmap-config-name-taken"


class KnowmapDocumentNotFound(KnowledgeError):
    code = "knowledge/knowmap-document-not-found"


class KnowmapBuilderKeyGroupProjectMismatch(KnowledgeError):
    """Builder key group does not belong to the Knowledge Map's project."""

    code = "knowledge/knowmap-builder-key-group-project-mismatch"


class KnowmapNoEmbeddingKey(KnowledgeError):
    """R11.13 — a Knowledge Map must resolve an embedding key from its builder
    key group at create: the corpus is chunked + graph entities embedded into the
    knowmap_{project_id} collection, so a config that can never embed is invalid."""

    code = "knowledge/knowmap-no-embedding-key"


class KnowmapEmbedDimensionConflict(KnowledgeError):
    """Every Knowledge Map in a project shares one knowmap_{project_id} Qdrant
    collection sized at first build; a config resolving to a different-dimension
    embedding model is rejected (Phase 2a D2 analogue, R11.18)."""

    code = "knowledge/knowmap-embed-dimension-conflict"


class KnowmapEmbeddingModelChangeBlocked(KnowledgeError):
    """F-13 (R11.19) — a Knowledge Map's builder Key Group cannot be swapped to a
    different embedding provider/model while the config holds indexed vectors.

    The Knowledge Map analogue of :class:`GraphRagEmbeddingModelChangeBlocked`:
    old-model vectors persist in ``knowmap_{project_id}`` until an unrelated
    corpus change triggers a build, so embedding queries with a new model against
    them silently collapses recall. Rejected at update (409) once the config has
    entered at least one build (``last_build_at IS NOT NULL``, fail-closed); a
    never-built config may change freely."""

    code = "knowledge/knowmap-embedding-model-change-blocked"


__all__ = [
    "CapabilityMismatch",
    "ChunkParamsImmutable",
    "ChunkParamsInvalid",
    "DocumentTooLarge",
    "EmbedDimensionConflict",
    "EmbedModelNotWhitelisted",
    "GraphRagAgentProjectMismatch",
    "GraphRagBuildBusy",
    "GraphRagBuildFailed",
    "GraphRagBuilderKeyGroupProjectMismatch",
    "GraphRagConfigAlreadyExists",
    "GraphRagCollectionDimensionMismatch",
    "GraphRagConfigNotFound",
    "GraphRagResetCompensationFailed",
    "KnowmapResetCompensationFailed",
    "GraphRagEmbedDimensionConflict",
    "GraphRagEmbeddingModelChangeBlocked",
    "GraphRagInvalidHalfLife",
    "GraphRagOwnerProjectMismatch",
    "IngestFailed",
    "KnowledgeError",
    "KnowmapBuilderKeyGroupProjectMismatch",
    "KnowmapConfigNameTaken",
    "KnowmapConfigNotFound",
    "KnowmapDocumentNotFound",
    "KnowmapEmbedDimensionConflict",
    "KnowmapEmbeddingModelChangeBlocked",
    "KnowmapNoEmbeddingKey",
    "RagCollectionDimensionMismatch",
    "RagConfigNameTaken",
    "RagConfigNotFound",
    "RagDocumentNotFound",
    "UnsupportedMime",
]
