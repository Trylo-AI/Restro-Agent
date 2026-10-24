from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main_api import create_app as create_api
from app.main_worker import create_app as create_worker


def _paths(app) -> set[str]:  # type: ignore[no-untyped-def]
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


def test_api_never_mounts_internal_routes() -> None:
    assert not any(p.startswith("/internal") for p in _paths(create_api()))


def test_worker_mounts_only_internal_and_health() -> None:
    assert all(p.startswith("/internal") or p == "/healthz" for p in _paths(create_worker()))


def test_healthz() -> None:
    assert TestClient(create_api()).get("/healthz").json() == {"status": "ok", "service": "api"}
    assert TestClient(create_worker()).get("/healthz").json() == {"status": "ok", "service": "worker"}
