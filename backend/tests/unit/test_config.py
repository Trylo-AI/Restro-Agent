from app.core.config import Settings


def _settings(**env: str) -> Settings:
    base = {
        "supabase_url": "http://127.0.0.1:54321",
        "supabase_secret_key": "k",
        "database_url": "postgresql://x",
        "meta_app_secret": "s",
        "meta_verify_token": "t",
    }
    return Settings(_env_file=None, **{**base, **env})  # type: ignore[arg-type]


def test_cors_origins_from_comma_separated_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://localhost:5174")
    assert _settings().cors_origins == ["http://localhost:5173", "http://localhost:5174"]


def test_simulator_never_on_outside_local() -> None:
    assert _settings(app_env="local", simulator_enabled="true").simulator_on
    assert not _settings(app_env="prod", simulator_enabled="true").simulator_on
    assert not _settings(app_env="local", simulator_enabled="true", wa_transport="meta").simulator_on


def test_jwt_urls() -> None:
    s = _settings(supabase_url="https://abc.supabase.co/")
    assert s.jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert s.jwt_issuer == "https://abc.supabase.co/auth/v1"
