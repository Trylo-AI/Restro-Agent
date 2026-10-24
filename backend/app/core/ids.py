"""Process identity for leases: one id per running worker instance."""

import os
import secrets
import socket

INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"


def lease_token() -> str:
    """Lease owner value: instance id + a random token, unique per claim."""
    return f"{INSTANCE_ID}:{secrets.token_hex(8)}"
