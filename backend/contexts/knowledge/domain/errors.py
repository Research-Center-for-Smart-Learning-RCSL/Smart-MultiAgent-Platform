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


class GraphRagBuilderKeyGroupConflict(KnowledgeError):
    """R11.01 — builder key group must differ from the consumer agent's key group."""

    code = "knowledge/graphrag-builder-key-group-conflict"


class GraphRagAgentProjectMismatch(KnowledgeError):
    """Agent referenced in the draft does not belong to the target project."""

    code = "knowledge/graphrag-agent-project-mismatch"


class GraphRagBuilderKeyGroupProjectMismatch(KnowledgeError):
    """Builder key group does not belong to the target project."""

    code = "knowledge/graphrag-builder-key-group-project-mismatch"


__all__ = [
    "CapabilityMismatch",
    "ChunkParamsInvalid",
    "DocumentTooLarge",
    "EmbedDimensionConflict",
    "EmbedModelNotWhitelisted",
    "GraphRagAgentProjectMismatch",
    "GraphRagBuildBusy",
    "GraphRagBuildFailed",
    "GraphRagBuilderKeyGroupConflict",
    "GraphRagBuilderKeyGroupProjectMismatch",
    "GraphRagConfigAlreadyExists",
    "GraphRagConfigNotFound",
    "IngestFailed",
    "KnowledgeError",
    "RagConfigNameTaken",
    "RagConfigNotFound",
    "RagDocumentNotFound",
    "UnsupportedMime",
]
