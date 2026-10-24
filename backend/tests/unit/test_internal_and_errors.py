from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import NotFound, VersionConflict, install_error_handlers
from app.main_worker import create_app as create_worker


def test_internal_routes_need_the_token() -> None:
    client = TestClient(create_worker())
    assert client.post("/internal/tasks/timer", json={"kind": "x"}).status_code == 401
    assert client.post("/internal/tasks/timer", json={"kind": "x"},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/internal/jobs/stuck_inbound_check").status_code == 401


def test_internal_route_with_token_reaches_the_handler() -> None:
    client = TestClient(create_worker())
    r = client.post("/internal/tasks/timer", json={"kind": "no_such_kind", "cafe_id": "c", "subject_id": "s"},
                    headers={"Authorization": "Bearer test-internal-token"})
    assert r.status_code == 200  # unknown kinds are logged and skipped, never retried forever


def test_error_body_shape() -> None:
    app = FastAPI()
    install_error_handlers(app)

    class Body(BaseModel):
        qty: int

    @app.post("/nf")
    async def nf() -> None:
        raise NotFound()

    @app.post("/conflict")
    async def conflict() -> None:
        raise VersionConflict(details={"current": {"version": 3}})

    @app.post("/validate")
    async def validate(body: Body) -> None:
        return None

    @app.post("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail that must not leak")

    c = TestClient(app, raise_server_exceptions=False)
    assert c.post("/nf").json() == {"error": {"code": "not_found", "message": "Not found."}}
    r = c.post("/conflict")
    assert r.status_code == 409 and r.json()["error"]["details"]["current"]["version"] == 3
    r = c.post("/validate", json={"qty": "two cold coffee"})
    assert r.status_code == 400 and "two cold coffee" not in r.text
    r = c.post("/boom")
    assert r.status_code == 500 and "secret detail" not in r.text
