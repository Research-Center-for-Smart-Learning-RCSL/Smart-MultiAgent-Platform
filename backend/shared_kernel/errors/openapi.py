"""OpenAPI correction for FastAPI's automatic request-validation responses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI

from shared_kernel.errors.problem import ValidationProblem

_DEFAULT_VALIDATION_REF = "#/components/schemas/HTTPValidationError"
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _validation_schemas() -> dict[str, Any]:
    problem_schema = ValidationProblem.model_json_schema(
        ref_template="#/components/schemas/{model}",
    )
    definitions = problem_schema.pop("$defs", {})
    return {**definitions, "ValidationProblem": problem_schema}


def _is_fastapi_validation_response(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    content = response.get("content")
    if not isinstance(content, dict) or set(content) != {"application/json"}:
        return False
    media = content["application/json"]
    return isinstance(media, dict) and media.get("schema") == {"$ref": _DEFAULT_VALIDATION_REF}


def install_validation_problem_openapi(app: FastAPI) -> None:
    """Make published automatic 422s match the installed runtime handler."""

    original_openapi: Callable[[], dict[str, Any]] = app.openapi

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema

        schema = original_openapi()
        replaced = False
        for path_item in schema.get("paths", {}).values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                responses = operation.get("responses")
                if not isinstance(responses, dict):
                    continue
                response = responses.get("422")
                if not _is_fastapi_validation_response(response):
                    continue
                response = cast(dict[str, Any], response)
                response["description"] = "Request Validation Problem"
                response["content"] = {
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/ValidationProblem"}}
                }
                replaced = True

        if replaced:
            schemas = schema.setdefault("components", {}).setdefault("schemas", {})
            schemas.update(_validation_schemas())
            schemas.pop("HTTPValidationError", None)
            schemas.pop("ValidationError", None)

        app.openapi_schema = schema
        return schema

    app.__dict__["openapi"] = _openapi
