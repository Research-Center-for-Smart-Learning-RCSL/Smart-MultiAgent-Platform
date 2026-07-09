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


class DocumentTooLarge(KnowledgeError):
    code = "knowledge/document-too-large"


class IngestFailed(KnowledgeError):
    code = "knowledge/ingest-failed"


class ChunkParamsInvalid(KnowledgeError):
    code = "knowledge/chunk-params-invalid"


class GraphRagConfigNotFound(KnowledgeError):
    code = "knowledge/graphrag-config-not-found"


class GraphRagBuildBusy(KnowledgeError):
    """R11a.01 — another build holds the Redis lock for this config."""

    code = "knowledge/graphrag-build-busy"


class GraphRagBuildFailed(KnowledgeError):
    """Builder entered a terminal `failed` state during the current run."""

    code = "knowledge/graphrag-build-failed"


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


__all__ = [
    "CapabilityMismatch",
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
    "GraphRagEmbedDimensionConflict",
    "GraphRagInvalidHalfLife",
    "GraphRagOwnerProjectMismatch",
    "IngestFailed",
    "KnowledgeError",
    "KnowmapBuilderKeyGroupProjectMismatch",
    "KnowmapConfigNameTaken",
    "KnowmapConfigNotFound",
    "KnowmapDocumentNotFound",
    "KnowmapEmbedDimensionConflict",
    "KnowmapNoEmbeddingKey",
    "RagConfigNameTaken",
    "RagConfigNotFound",
    "RagDocumentNotFound",
    "UnsupportedMime",
]
