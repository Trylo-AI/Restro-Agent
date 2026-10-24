from fastapi import HTTPException

from app.api.internal import router
from app.domain.jobs import JOBS, run_job


@router.post("/jobs/{job}")
async def job(job: str) -> dict[str, object]:
    if job not in JOBS:
        raise HTTPException(status_code=404)
    return {"job": job, "result": await run_job(job)}
