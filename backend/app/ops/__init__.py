"""Performance dashboard (docs/OPS_PLAN.md). `tracing/` writes runs and spans; this package's read side
(`api`, `queries`, `insights`, `logs`) serves /api/ops and never calls business logic.
"""
