"""/api/ops access (PDS §14) and the JSONL log reader (PDS §17)."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import get_settings
from app.ops import api, logs


def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_ops_api_is_off_without_a_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ops_dashboard_token", None)
    assert client().get("/api/ops/meta", headers={"X-Ops-Token": "anything"}).status_code == 404


def test_ops_api_needs_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ops_dashboard_token", SecretStr("s3cret-token"))
    c = client()
    assert c.get("/api/ops/meta").status_code == 401
    assert c.get("/api/ops/meta", headers={"X-Ops-Token": "wrong"}).status_code == 401
    r = c.get("/api/ops/meta", headers={"X-Ops-Token": "s3cret-token"})
    assert r.status_code == 200
    body = r.json()
    assert (body["standard"], body["version"]) == ("PDS", "1.0") and body["tables_ok"] is False  # no pool here
    assert "turn.route" in body["registry"]["spans"] and body["features"]["cost"] is False


def _write(path: Path, stamps: list[str]) -> None:
    path.write_text("".join(json.dumps({"timestamp": t, "level": "info", "event": f"e{t}"}) + "\n" for t in stamps),
                    encoding="utf-8")


def test_log_reader_crosses_rotation_and_pages_without_duplicates(tmp_path: Path) -> None:
    _write(tmp_path / "api.jsonl.1", [f"2026-10-25T01:00:{s:02d}" for s in range(0, 30)])
    _write(tmp_path / "api.jsonl", [f"2026-10-25T01:00:{s:02d}" for s in range(30, 60)])
    first = logs.read(limit=40, directory=tmp_path)
    assert first["lines"][0]["timestamp"] == "2026-10-25T01:00:59" and len(first["lines"]) == 40
    second = logs.read(limit=40, before=first["cursor"], directory=tmp_path)
    seen = [x["timestamp"] for x in first["lines"] + second["lines"]]
    assert len(seen) == len(set(seen)) == 60
    newer = logs.read(after="2026-10-25T01:00:57", directory=tmp_path)
    assert [x["timestamp"] for x in newer["lines"]] == ["2026-10-25T01:00:59", "2026-10-25T01:00:58"]
    assert logs.read(level="error", directory=tmp_path)["lines"] == []
    assert logs.read(directory=tmp_path / "missing")["available"] is False
