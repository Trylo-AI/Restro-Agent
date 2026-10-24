"""Console auth (docs/rules.md#unit-and-integration, Auth row) with a local ES256 key standing in for the JWKS."""

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import auth
from app.core.auth import Principal
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.db.repositories.memberships import Membership

CAFE_A = str(uuid.uuid4())
CAFE_B = str(uuid.uuid4())
USER = str(uuid.uuid4())
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
KID = "test-kid"


class FakeJwks(auth.JwksCache):
    async def fetch(self) -> dict[str, object]:
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key()))
        jwk.update(kid=KID, alg="ES256", use="sig")
        return {"keys": [jwk]}


def make_token(*, key=KEY, sub: str = USER, aud: str = "authenticated", exp_in: int = 3600,  # type: ignore[no-untyped-def]
               iss: str | None = None) -> str:
    s = get_settings()
    claims = {"sub": sub, "aud": aud, "iss": iss or s.jwt_issuer, "exp": int(time.time()) + exp_in, "role": "authenticated"}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": KID})


MEMBERSHIPS = {
    (CAFE_A, USER): Membership(USER, CAFE_A, "shift_manager", True, "Ravi"),
}


async def fake_loader(cafe_id: str, user_id: str) -> Membership | None:
    return MEMBERSHIPS.get((cafe_id, user_id))


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(auth, "_jwks", FakeJwks("http://jwks.test"))
    app = FastAPI()
    install_error_handlers(app)
    app.dependency_overrides[auth.membership_loader] = lambda: fake_loader

    @app.post("/shift")
    async def shift(p: Principal = Depends(auth.shift_manager)) -> dict[str, str]:
        return {"cafe_id": p.cafe_id, "role": p.role}

    @app.post("/manager")
    async def manager(p: Principal = Depends(auth.manager)) -> dict[str, str]:
        return {"role": p.role}

    return TestClient(app)


def call(client: TestClient, path: str, token: str | None, cafe: str | None = CAFE_A):  # type: ignore[no-untyped-def]
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cafe:
        headers["X-Cafe-Id"] = cafe
    return client.post(path, headers=headers)


def test_valid_member(client: TestClient) -> None:
    r = call(client, "/shift", make_token())
    assert r.status_code == 200 and r.json() == {"cafe_id": CAFE_A, "role": "shift_manager"}


def test_missing_token_is_401(client: TestClient) -> None:
    r = call(client, "/shift", None)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("kwargs", [
    {"exp_in": -10},                       # expired
    {"aud": "anon"},                       # wrong audience
    {"iss": "https://evil.example/auth/v1"},  # wrong issuer
    {"key": OTHER_KEY},                    # signed by someone else
])
def test_bad_tokens_are_401(client: TestClient, kwargs: dict[str, object]) -> None:
    assert call(client, "/shift", make_token(**kwargs)).status_code == 401  # type: ignore[arg-type]


def test_hs256_token_is_refused(client: TestClient) -> None:
    token = jwt.encode({"sub": USER, "aud": "authenticated", "exp": int(time.time()) + 60}, "x" * 32, algorithm="HS256")
    assert call(client, "/shift", token).status_code == 401


def test_other_cafe_is_403(client: TestClient) -> None:
    r = call(client, "/shift", make_token(), cafe=CAFE_B)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


def test_missing_or_bad_cafe_header_is_404(client: TestClient) -> None:
    assert call(client, "/shift", make_token(), cafe=None).status_code == 404
    assert call(client, "/shift", make_token(), cafe="not-a-uuid").status_code == 404


def test_role_too_low_is_403(client: TestClient) -> None:
    assert call(client, "/manager", make_token()).status_code == 403


def test_deactivated_membership_is_403(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(MEMBERSHIPS, (CAFE_A, USER), Membership(USER, CAFE_A, "owner", False, "Ravi"))
    assert call(client, "/shift", make_token()).status_code == 403
