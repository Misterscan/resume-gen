from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
from uuid import uuid4

from django.core.cache import cache

from services.exceptions import ServiceUnavailableError

executor = ThreadPoolExecutor(max_workers=4)
logger = logging.getLogger(__name__)


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
        except ServiceUnavailableError as exc:
            logger.warning("Job deferred because upstream service is unavailable: %s", exc)
            cache.set(
                f"job:{job_id}",
                JobStatus(status="failed", error=str(exc)).__dict__,
                timeout=3600,
            )
        except Exception as e:
            logger.error("Job failed", exc_info=True)
            cache.set(
                f"job:{job_id}",
                JobStatus(status="failed", error=str(e)).__dict__,
                timeout=3600,
            )

    executor.submit(runner)
    return job_id


def get_job(job_id: str) -> dict:
    return cache.get(f"job:{job_id}") or {"status": "missing"}