"""/audit/* and /orders/{id}/history: owner only (D15) and query validation → 400, without a database.

A local ES256 key stands in for the JWKS and memberships come from a dict (as in test_auth.py). Every request below is
refused before any database read, so no pool is needed.
"""

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.staff import router
from app.core import auth
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.db.repositories.memberships import Membership

CAFE = str(uuid.uuid4())
USERS = {role: str(uuid.uuid4()) for role in ("shift_manager", "manager", "owner")}
KEY = ec.generate_private_key(ec.SECP256R1())
KID = "audit-test-kid"
ROUTES = ["/audit/entries", "/audit/facets", "/audit/catalog", "/audit/export", f"/orders/{uuid.uuid4()}/history"]


class FakeJwks(auth.JwksCache):
    async def fetch(self) -> dict[str, object]:
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key()))
        jwk.update(kid=KID, alg="ES256", use="sig")
        return {"keys": [jwk]}


async def fake_loader(cafe_id: str, user_id: str) -> Membership | None:
    role = next((r for r, u in USERS.items() if u == user_id), None)
    return Membership(user_id, cafe_id, role, True, role.title()) if role and cafe_id == CAFE else None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(auth, "_jwks", FakeJwks("http://jwks.test"))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[auth.membership_loader] = lambda: fake_loader
    return TestClient(app)


def headers(role: str) -> dict[str, str]:
    s = get_settings()
    claims = {"sub": USERS[role], "aud": "authenticated", "iss": s.jwt_issuer, "exp": int(time.time()) + 600,
              "role": "authenticated"}
    return {"Authorization": f"Bearer {jwt.encode(claims, KEY, algorithm='ES256', headers={'kid': KID})}", "X-Cafe-Id": CAFE}


@pytest.mark.parametrize("role", ["shift_manager", "manager"])
@pytest.mark.parametrize("path", ROUTES)
def test_audit_routes_owner_only(client: TestClient, role: str, path: str) -> None:
    r = client.get(path, headers=headers(role))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.get(path).status_code == 401


def test_owner_reads_the_catalog(client: TestClient) -> None:
    r = client.get("/audit/catalog", headers=headers("owner"))
    assert r.status_code == 200
    body = r.json()
    assert {a["action"] for a in body["actions"]} >= {"order.accept", "order.cancel", "chat.takeover"}
    assert body["severities"] == ["critical", "warning", "info"]
    assert {c["id"] for c in body["categories"]} >= {"orders", "other"}


@pytest.mark.parametrize("query", [
    "severity=urgent", "category=kitchen", "role=customer", "limit=0", "limit=201", "actor_id=not-a-uuid",
    "action=Order.Cancel", "action=order", "order_number=0", "order_id=42", "customer_id=x", "conversation_id=x",
    "from=yesterday", "q=" + "x" * 61, "before=" + "x" * 121,
    "from=2026-11-01T10:00:00Z&to=2026-11-01T09:00:00Z", "from=2025-01-01T00:00:00Z&to=2026-11-01T00:00:00Z",
    "before=bm90LWEtY3Vyc29y", "&".join(f"action=order.a{i}" for i in range(41)),
    "&".join(f"actor_id={uuid.uuid4()}" for _ in range(21)),
])
def test_query_validation_400(client: TestClient, query: str) -> None:
    r = client.get(f"/audit/entries?{query}", headers=headers("owner"))
    assert r.status_code == 400, (query, r.text)
    assert r.json()["error"]["code"] == "bad_request"


def test_query_validation_400_on_facets_export_and_history(client: TestClient) -> None:
    owner = headers("owner")
    assert client.get("/audit/facets?from=2026-11-02T00:00:00Z&to=2026-11-01T00:00:00Z", headers=owner).status_code == 400
    assert client.get("/audit/export?severity=urgent", headers=owner).status_code == 400
    assert client.get("/audit/export?from=2026-11-02T00:00:00Z&to=2026-11-01T00:00:00Z", headers=owner).status_code == 400
    assert client.get("/orders/not-a-uuid/history", headers=owner).status_code == 400
