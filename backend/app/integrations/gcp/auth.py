"""OAuth access tokens for Google REST APIs from the service's own identity: the Cloud Run metadata server in GCP,
Application Default Credentials on a laptop. No service-account keys anywhere (docs/architecture.md#infrastructure).
Tokens are refreshed by google-auth before they expire and never logged."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

SCOPE = "https://www.googleapis.com/auth/cloud-platform"

TokenSource = Callable[[], Awaitable[str]]


class AdcTokens:
    def __init__(self) -> None:
        self._creds: Any = None
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if self._creds is None:
                import google.auth

                self._creds, _ = await asyncio.to_thread(google.auth.default, scopes=[SCOPE])
            if not self._creds.valid:  # google-auth refreshes a few minutes before expiry
                from google.auth.transport.requests import Request

                await asyncio.to_thread(self._creds.refresh, Request())
            return str(self._creds.token)


class GcpError(Exception):
    """A Google API call failed. The message holds the status and Google's error status only, never the body."""
