from unittest.mock import Mock, patch

from django.core.cache import cache

from builder.jobs import get_job, submit_job
from services.exceptions import ServiceUnavailableError


def test_submit_job_handles_service_unavailable_without_error_log():
    cache.clear()

    def run_immediately(fn):
        fn()
        return Mock()

    with patch("builder.jobs.executor.submit", side_effect=run_immediately), patch(
        "builder.jobs.logger"
    ) as mock_logger:
        job_id = submit_job(
            lambda: (_ for _ in ()).throw(
                ServiceUnavailableError(
                    "Gemini API is overloaded right now. Please try again in a minute."
                )
            )
        )

    job = get_job(job_id)

    assert job["status"] == "failed"
    assert "Gemini API is overloaded right now" in job["error"]
    mock_logger.warning.assert_called_once()
    mock_logger.error.assert_not_called()