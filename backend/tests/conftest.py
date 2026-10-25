"""Test settings: synthetic values only, never the developer's backend/.env."""

import os

TEST_ENV = {
    "APP_ENV": "test",
    "SUPABASE_URL": "http://127.0.0.1:54321",
    "SUPABASE_SECRET_KEY": "test-secret-key",
    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
    "DATABASE_URL_SESSION": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
    "LLM_BACKEND": "stub",
    "META_APP_SECRET": "test-app-secret",
    "META_VERIFY_TOKEN": "test-verify-token",
    "INTERNAL_DEV_TOKEN": "test-internal-token",
    "SIMULATOR_ENABLED": "true",
    "SIMULATOR_TOKEN": "test-sim-token",
    "WA_TRANSPORT": "simulator",
}

# Keep pydantic-settings from reading backend/.env during tests.
os.environ["RESTRO_TEST"] = "1"
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)

# Ops runs written by tests are tagged service=test-*, which the dashboard hides unless asked for.
from app.ops.tracing import recorder  # noqa: E402

recorder.configure("test")
