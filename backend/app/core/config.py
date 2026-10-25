"""Typed settings from the environment (backend/.env locally, Secret Manager-backed env in the cloud).

Every switch in docs/ordering-slice.md#swappable-adapters lives here. Values are never logged.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "staging", "prod"] = "local"
    log_level: str = "info"

    # Supabase
    supabase_url: str
    supabase_secret_key: SecretStr
    supabase_jwt_audience: str = "authenticated"
    database_url: SecretStr
    database_url_session: SecretStr | None = None

    # LLM
    llm_backend: Literal["developer_api", "vertex", "stub", "stub_menu"] = "developer_api"
    llm_stub_delay_ms: int = 900  # stub_menu only: roughly a Flash call
    gemini_api_key: SecretStr | None = None
    gcp_project: str | None = None
    gcp_location: str = "global"
    # Gemini 2.5 is closed to new API keys (Oct 2026); routing is decided in Phase 2.
    llm_model_primary: str = "gemini-3.5-flash"
    llm_model_fallback: str = "gemini-3.5-flash-lite"
    llm_timeout_seconds: float = 6.0
    llm_kill_switch: Literal["off", "cheap", "handoff"] = "off"
    llm_daily_spend_limit_usd: float = 2.0

    # WhatsApp
    wa_transport: Literal["simulator", "meta"] = "simulator"
    meta_app_secret: SecretStr
    meta_verify_token: SecretStr
    meta_graph_version: str = "v21.0"

    # Signal, tasks, internal auth
    signal_backend: Literal["local", "pubsub"] = "local"
    tasks_backend: Literal["local", "cloud_tasks"] = "local"
    worker_url: str = "http://localhost:8001"
    api_url: str = "http://localhost:8000"
    gcp_tasks_location: str = "asia-south1"  # Cloud Tasks queues (Vertex AI uses gcp_location)
    gcp_invoker_sa: str | None = None  # invoker-sa email: OIDC identity of tasks and pushes to worker
    pubsub_topic: str = "wa-inbound"
    internal_auth: Literal["dev_token", "oidc"] = "dev_token"
    internal_dev_token: SecretStr | None = None

    # Secrets
    secrets_backend: Literal["env", "gcp"] = "env"

    # Performance dashboard (docs/OPS_PLAN.md)
    ops_tracing_enabled: bool = True  # kill switch: false makes every tracing call a no-op
    ops_dashboard_token: SecretStr | None = None  # /api/ops/* is off unless set (platform_admin login later)
    ops_log_dir: str | None = None  # local only: JSON-lines logs for the Logs page; default backend/logs
    ops_task_max_attempts: int = 6  # a task attempt below this that fails is RETRYING, at it FAILED

    # Local dev tools
    simulator_enabled: bool = False
    simulator_token: SecretStr | None = None
    dev_traces: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)  # comma-separated in .env

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_local(self) -> bool:
        return self.app_env in ("local", "test")

    @property
    def simulator_on(self) -> bool:
        """The simulator and /dev/* exist only locally, whatever the flag says."""
        return self.is_local and self.simulator_enabled and self.wa_transport == "simulator"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    # Tests set RESTRO_TEST so the developer's backend/.env is never read.
    env_file = None if os.environ.get("RESTRO_TEST") else ENV_FILE
    return Settings(_env_file=env_file)  # type: ignore[call-arg]
