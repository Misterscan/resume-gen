from unittest.mock import patch

from django.test import RequestFactory

from builder.views import compute_resume_cache_hash, generate
from services.exceptions import ServiceUnavailableError


@patch("builder.views.build_resume_from_payload")
@patch("builder.views.parse_resume_form")
def test_generate_returns_503_for_service_overload(mock_parse_resume_form, mock_build_resume):
    mock_parse_resume_form.return_value = (
        {},
        {
            "mode": "create",
            "generate_cl": False,
            "target_role": "",
            "target_company": "",
            "revision_notes": "",
        },
        b"",
    )
    mock_build_resume.side_effect = ServiceUnavailableError(
        "Gemini API is overloaded right now. Please try again in a minute."
    )

    request = RequestFactory().post("/generate/")
    request.session = {}

    response = generate(request)

    assert response.status_code == 503
    assert b"Gemini API is overloaded right now" in response.content


def test_resume_cache_hash_ignores_cover_letter_and_targeting_options():
    raw_data = {"full_name": "Jane Doe", "summary": "Engineer"}
    file_hash = b"abc123"

    options_a = {
        "mode": "scratch",
        "revision_notes": "",
        "generate_cl": False,
        "target_role": "Backend Engineer",
        "target_company": "Acme",
    }
    options_b = {
        "mode": "scratch",
        "revision_notes": "",
        "generate_cl": True,
        "target_role": "Platform Engineer",
        "target_company": "Other",
    }

    assert compute_resume_cache_hash(raw_data, options_a, file_hash) == compute_resume_cache_hash(
        raw_data,
        options_b,
        file_hash,
    )


def test_resume_cache_hash_changes_when_revision_notes_change():
    raw_data = {"full_name": "Jane Doe", "summary": "Engineer"}
    file_hash = b"abc123"

    options_a = {
        "mode": "revise",
        "revision_notes": "Tailor for staff roles",
    }
    options_b = {
        "mode": "revise",
        "revision_notes": "Tailor for manager roles",
    }

    assert compute_resume_cache_hash(raw_data, options_a, file_hash) != compute_resume_cache_hash(
        raw_data,
        options_b,
        file_hash,
    )