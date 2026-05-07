from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import uuid4

from django.core.cache import cache

executor = ThreadPoolExecutor(max_workers=4)


@dataclass(frozen=True)
class JobStatus:
    status: str
    result: dict | None = None
    error: str | None = None


def submit_job(fn, *args, **kwargs) -> str:
    job_id = uuid4().hex
    cache.set(f"job:{job_id}", JobStatus(status="queued").__dict__, timeout=3600)

    def runner():
        cache.set(f"job:{job_id}", JobStatus(status="running").__dict__, timeout=3600)
        try:
            result = fn(*args, **kwargs)
            cache.set(
                f"job:{job_id}",
                JobStatus(status="complete", result=result).__dict__,
                timeout=3600,
            )
        except Exception:
            cache.set(
                f"job:{job_id}",
                JobStatus(status="failed", error="Job failed.").__dict__,
                timeout=3600,
            )

    executor.submit(runner)
    return job_id


def get_job(job_id: str) -> dict:
    return cache.get(f"job:{job_id}") or {"status": "missing"}