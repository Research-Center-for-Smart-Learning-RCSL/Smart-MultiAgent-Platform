from typing import Annotated

from fastapi import Cookie, FastAPI, Header, Path, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, model_validator

from app.main import create_app
from shared_kernel.errors import NotImplementedProblem, SmapError, problem_type
from shared_kernel.errors.handlers import register_exception_handlers


def _app_raising(exc: Exception) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def _boom() -> None:
        raise exc

    return app


def test_problem_type_prefix_is_canonical() -> None:
    assert problem_type("foo").startswith("https://smap.local/problems/")


def test_smap_error_returns_problem_json() -> None:
    err = NotImplementedProblem()
    client = TestClient(_app_raising(err))
    r = client.get("/boom")
    assert r.status_code == 501
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "https://smap.local/problems/not-implemented"
    assert body["title"] == "Not Implemented"
    assert body["status"] == 501
    assert body["instance"] == "/boom"


def test_unhandled_becomes_500() -> None:
    client = TestClient(_app_raising(ValueError("kaboom")), raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["type"] == "https://smap.local/problems/internal"
    assert "kaboom" not in body["detail"]  # no leak


def test_custom_extras_do_not_overwrite_reserved() -> None:
    err = SmapError(
        type_=problem_type("x"),
        title="X",
        status=400,
        extras={"type": "malicious", "hint": "ok"},
    )
    body = err.problem.dump()
    assert body["type"] == "https://smap.local/problems/x"
    assert body["hint"] == "ok"


class _ValidationItem(BaseModel):
    count: int


class _ValidationBody(BaseModel):
    items: list[_ValidationItem]
    name: str = Field(min_length=2)

    @model_validator(mode="after")
    def _name_is_not_root_error(self) -> "_ValidationBody":
        if self.name == "root-error":
            raise ValueError("body-level validation failed")
        return self


def _validation_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/validation/{resource_id}")
    def _validate(
        body: _ValidationBody,
        resource_id: Annotated[int, Path()],
        limit: Annotated[int, Query()],
        x_count: Annotated[int, Header()],
        session_id: Annotated[int, Cookie()],
    ) -> None:
        return None

    return app


def test_request_validation_errors_use_safe_field_error_contract() -> None:
    client = TestClient(_validation_app())
    secret_input = "secret-input-must-not-leak"

    response = client.post(
        "/validation/not-an-int?limit=not-an-int",
        headers={"x-count": "not-an-int"},
        cookies={"session_id": "not-an-int"},
        json={"items": [{"count": secret_input}], "name": "x"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body == {
        "type": "https://smap.local/problems/validation",
        "title": "Validation Failed",
        "status": 422,
        "detail": "Request validation failed.",
        "instance": "/validation/not-an-int",
        "field_errors": [
            {
                "path": "resource_id",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
            {
                "path": "limit",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
            {
                "path": "x-count",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
            {
                "path": "session_id",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
            {
                "path": "items[0].count",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
            {"path": "name", "message": "String should have at least 2 characters"},
        ],
    }
    assert secret_input not in response.text
    assert '"input"' not in response.text


def test_root_validation_error_is_not_claimed_as_a_field_error() -> None:
    client = TestClient(_validation_app())
    response = client.post(
        "/validation/1?limit=1",
        headers={"x-count": "1"},
        cookies={"session_id": "1"},
        json={"items": [{"count": 1}], "name": "root-error"},
    )

    assert response.status_code == 422
    assert response.json()["field_errors"] == []


def test_openapi_advertises_the_runtime_validation_problem_contract() -> None:
    schema = create_app().openapi()
    response = schema["paths"]["/api/auth/login"]["post"]["responses"]["422"]

    assert set(response["content"]) == {"application/problem+json"}
    assert response["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/ValidationProblem"
    }
    field_error = schema["components"]["schemas"]["ValidationFieldError"]
    assert set(field_error["properties"]) == {"path", "message"}
    assert set(field_error["required"]) == {"path", "message"}
    validation_problem = schema["components"]["schemas"]["ValidationProblem"]
    assert set(validation_problem["required"]) == {
        "type",
        "title",
        "status",
        "detail",
        "field_errors",
    }
    assert "HTTPValidationError" not in schema["components"]["schemas"]
