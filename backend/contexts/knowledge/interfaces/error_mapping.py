"""Knowledge domain errors → RFC 7807 registration.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3).
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.knowledge.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.RagConfigNotFound: (
        "knowledge/rag-config-not-found",
        404,
        "RAG config not found",
    ),
    errors.RagConfigNameTaken: (
        "knowledge/rag-config-name-taken",
        409,
        "RAG config name in use",
    ),
    errors.RagDocumentNotFound: (
        "knowledge/rag-document-not-found",
        404,
        "RAG document not found",
    ),
    errors.UnsupportedMime: (
        "knowledge/unsupported-mime",
        415,
        "Unsupported document type",
    ),
    errors.EmbedModelNotWhitelisted: (
        "knowledge/embed-model-not-whitelisted",
        422,
        "Embedding model is not on the whitelist",
    ),
    errors.CapabilityMismatch: (
        "capability-mismatch",
        422,
        "API key capability mismatch",
    ),
    errors.EmbedDimensionConflict: (
        "knowledge/embed-dimension-conflict",
        422,
        "All RAG configs in a project must share one embedding dimension",
    ),
    errors.RagCollectionDimensionMismatch: (
        "knowledge/rag-collection-dimension-mismatch",
        500,
        "RAG ingest produced vectors of the wrong dimension for the project collection",
    ),
    errors.DocumentTooLarge: (
        "knowledge/document-too-large",
        413,
        "Document too large; use tus",
    ),
    errors.IngestFailed: (
        "knowledge/ingest-failed",
        500,
        "Document ingest failed",
    ),
    errors.ChunkParamsInvalid: (
        "knowledge/chunk-params-invalid",
        422,
        "Invalid chunk parameters",
    ),
    errors.ChunkParamsImmutable: (
        "knowledge/chunk-params-immutable",
        409,
        "Chunk parameters are fixed once the config has documents",
    ),
    errors.GraphRagConfigNotFound: (
        "knowledge/graphrag-config-not-found",
        404,
        "GraphRAG config not found",
    ),
    errors.GraphRagBuildBusy: (
        "knowledge/graphrag-build-busy",
        409,
        "A GraphRAG build is already in progress for this config",
    ),
    errors.GraphRagBuildFailed: (
        "knowledge/graphrag-build-failed",
        500,
        "GraphRAG build failed",
    ),
    errors.GraphRagResetCompensationFailed: (
        "knowledge/graphrag-reset-compensation-failed",
        503,
        "Admin reset could not compensate the config's external state; retry or force",
    ),
    errors.KnowmapResetCompensationFailed: (
        "knowledge/knowmap-reset-compensation-failed",
        503,
        "Admin reset could not compensate the config's external state; retry or force",
    ),
    errors.GraphRagConfigAlreadyExists: (
        "knowledge/graphrag-config-already-exists",
        409,
        "GraphRAG config already exists for this agent",
    ),
    errors.GraphRagAgentProjectMismatch: (
        "knowledge/graphrag-agent-project-mismatch",
        422,
        "Agent does not belong to the target project",
    ),
    errors.GraphRagBuilderKeyGroupProjectMismatch: (
        "knowledge/graphrag-builder-key-group-project-mismatch",
        422,
        "Builder key group does not belong to the target project",
    ),
    errors.GraphRagEmbedDimensionConflict: (
        "knowledge/graphrag-embed-dimension-conflict",
        422,
        "All GraphRAG configs in a project must share one embedding dimension",
    ),
    errors.GraphRagEmbeddingModelChangeBlocked: (
        "knowledge/graphrag-embedding-model-change-blocked",
        409,
        "Cannot change the embedding model of a Concept Map that has indexed data",
    ),
    errors.GraphRagOwnerProjectMismatch: (
        "knowledge/graphrag-owner-project-mismatch",
        422,
        "Owner does not belong to the target project",
    ),
    errors.GraphRagInvalidHalfLife: (
        "knowledge/graphrag-invalid-half-life",
        422,
        "recency_half_life_days must be a positive number",
    ),
    errors.GraphRagCollectionDimensionMismatch: (
        "knowledge/graphrag-collection-dimension-mismatch",
        500,
        "GraphRAG build produced vectors of the wrong dimension for the project collection",
    ),
    # Knowledge Map (Phase 3)
    errors.KnowmapConfigNotFound: (
        "knowledge/knowmap-config-not-found",
        404,
        "Knowledge Map config not found",
    ),
    errors.KnowmapConfigNameTaken: (
        "knowledge/knowmap-config-name-taken",
        409,
        "Knowledge Map config name in use",
    ),
    errors.KnowmapDocumentNotFound: (
        "knowledge/knowmap-document-not-found",
        404,
        "Knowledge Map document not found",
    ),
    errors.KnowmapBuilderKeyGroupProjectMismatch: (
        "knowledge/knowmap-builder-key-group-project-mismatch",
        422,
        "Builder key group does not belong to the target project",
    ),
    errors.KnowmapNoEmbeddingKey: (
        "knowledge/knowmap-no-embedding-key",
        422,
        "The builder key group resolves no embedding model for this Knowledge Map",
    ),
    errors.KnowmapEmbedDimensionConflict: (
        "knowledge/knowmap-embed-dimension-conflict",
        422,
        "All Knowledge Maps in a project must share one embedding dimension",
    ),
    errors.KnowmapEmbeddingModelChangeBlocked: (
        "knowledge/knowmap-embedding-model-change-blocked",
        409,
        "Cannot change the embedding model of a Knowledge Map that has indexed data",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.KnowledgeError, _MAP)


__all__ = ["register"]
