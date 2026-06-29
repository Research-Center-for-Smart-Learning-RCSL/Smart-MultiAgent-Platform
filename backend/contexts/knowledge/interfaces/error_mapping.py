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
    errors.GraphRagConfigAlreadyExists: (
        "knowledge/graphrag-config-already-exists",
        409,
        "GraphRAG config already exists for this agent",
    ),
    errors.GraphRagBuilderKeyGroupConflict: (
        "knowledge/graphrag-builder-key-group-conflict",
        422,
        "Builder key group must differ from the agent's consumer key group",
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
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.KnowledgeError, _MAP)


__all__ = ["register"]
