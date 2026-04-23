from fastapi import FastAPI
from fastapi.testclient import TestClient

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
        type_=problem_type("x"), title="X", status=400,
        extras={"type": "malicious", "hint": "ok"},
    )
    body = err.problem.dump()
    assert body["type"] == "https://smap.local/problems/x"
    assert body["hint"] == "ok"
