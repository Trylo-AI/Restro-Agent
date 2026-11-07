"""Console REST routes (contracts/openapi.yaml). Thin: auth → validate → one domain call → map errors.

Never accept cafe_id, role, prices or totals from the client (docs/rules.md: api/).
"""

from fastapi import APIRouter

router = APIRouter()

from app.api.staff import audit, cafes, campaigns, customers, help, menu, offers, orders  # noqa: E402,F401  (registers routes)
