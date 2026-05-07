"""Knowledge domain errors → RFC 7807 registration."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.knowledge.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.KnowledgeError], tuple[str, int, str]] = {
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
}


async def _handler(request: Request, exc: errors.KnowledgeError) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc),
        ("knowledge/generic", 400, "Knowledge error"),
    )
    problem = Problem(
        type=problem_type(slug),
        title=title,
        status=status,
        detail=str(exc),
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.KnowledgeError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
