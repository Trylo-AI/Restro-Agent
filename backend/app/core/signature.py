"""Meta webhook signature: HMAC-SHA256 of the raw body with the app secret, compared in constant time.

Checked before any JSON parsing (docs/backend.md#webhook).
"""

import hashlib
import hmac


def sign(raw_body: bytes, app_secret: str) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(sign(raw_body, app_secret), header)
