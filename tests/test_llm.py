import pytest
from unittest.mock import patch, MagicMock
from google.genai.errors import ServerError
from services.llm import (
    call_gemini_json,
    generate_resume_content,
    revise_resume_content,
    generate_cover_letter_content,
    verify_ats_compatibility,
)
from services.exceptions import IntegrationError, ServiceUnavailableError, ValidationError


VALID_RESUME_JSON = """
{
    "professional_summary": "Test Summary",
    "work_experience": [],
    "education": [],
    "skills": ["Python"]
}
"""

VALID_REVISION_JSON = """
{
    "candidate": {
        "full_name": "Test User",
        "contact_info": "new@example.com"
    },
    "resume": {
        "professional_summary": "Revised Summary",
        "work_experience": [],
        "education": [],
        "skills": ["Python", "Django"]
    }
}
"""

VALID_COVER_LETTER_JSON = """
{
    "recipient_info": "Hiring Team",
    "greeting": "Hello,",
    "introduction": "Intro",
    "body_paragraphs": ["Body 1"],
    "company_connection": "Connection",
    "closing": "Closing",
    "sign_off": "Best"
}
"""

VALID_ATS_JSON = """
{
    "ats_score": 82,
    "keyword_match_rate": "75%",
    "missing_keywords": ["Kubernetes", "CI/CD"],
    "formatting_feedback": ["Use more quantified metrics."],
    "content_feedback": ["Avoid the phrase 'results-driven'."],
    "overall_recommendation": "Strong resume. Add missing keywords for a better match."
}
"""


def _make_mock_client(response_text):
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    return mock_client


@patch("services.llm.genai.Client")
def test_generate_resume_content_success(mock_genai_client):
    mock_genai_client.return_value = _make_mock_client(VALID_RESUME_JSON)

    result = generate_resume_content(
        raw_data={"full_name": "John Doe", "contact_info": "john@example.com"},
        api_key="dummy_key",
    )

    assert result["professional_summary"] == "Test Summary"
    assert "Python" in result["skills"]
    mock_genai_client.return_value.models.generate_content.assert_called_once()


@patch("services.llm.genai.Client")
def test_generate_resume_schema_validation_failure(mock_genai_client):
    mock_genai_client.return_value = _make_mock_client('{"bad_json_structure"}')

    with pytest.raises((ValidationError, IntegrationError)):
        generate_resume_content(raw_data={"full_name": "John Doe"}, api_key="dummy_key")


@patch("services.llm.genai.Client")
def test_revise_resume_content_success(mock_genai_client):
    mock_genai_client.return_value = _make_mock_client(VALID_REVISION_JSON)

    result = revise_resume_content(
        api_key="dummy_key",
        revision_notes="Add Django to skills",
        current_resume={"professional_summary": "Old Summary"},
    )

    assert result["resume"]["professional_summary"] == "Revised Summary"
    assert "Django" in result["resume"]["skills"]


@patch("services.llm.genai.Client")
def test_generate_cover_letter_content(mock_genai_client):
    mock_genai_client.return_value = _make_mock_client(VALID_COVER_LETTER_JSON)

    result = generate_cover_letter_content(
        raw_data={},
        resume={},
        revision_notes="",
        target_role="Developer",
        target_company="Acme Corp",
        api_key="dummy_key",
    )

    assert result["recipient_info"] == "Hiring Team"
    assert result["introduction"] == "Intro"


@patch("services.llm.genai.Client")
def test_verify_ats_compatibility_success(mock_genai_client):
    mock_genai_client.return_value = _make_mock_client(VALID_ATS_JSON)

    result = verify_ats_compatibility(
        resume={"professional_summary": "Test Summary", "skills": ["Python"]},
        target_role="DevOps Engineer",
        job_description="Looking for Kubernetes and CI/CD experience.",
        api_key="dummy_key",
    )

    assert result["ats_score"] == 82
    assert result["keyword_match_rate"] == "75%"
    assert "Kubernetes" in result["missing_keywords"]
    assert isinstance(result["formatting_feedback"], list)
    assert isinstance(result["content_feedback"], list)
    assert result["overall_recommendation"] != ""


@patch("services.llm.genai.Client")
def test_verify_ats_compatibility_general_scan(mock_genai_client):
    """ATS check with no target role or job description (general scan)."""
    mock_genai_client.return_value = _make_mock_client(VALID_ATS_JSON)

    result = verify_ats_compatibility(
        resume={"professional_summary": "Test", "skills": []},
        target_role="",
        job_description="",
        api_key="dummy_key",
    )

    assert isinstance(result["ats_score"], int)
    assert 0 <= result["ats_score"] <= 100


@patch("services.llm.genai.Client")
def test_call_gemini_json_does_not_outer_retry_on_503(mock_genai_client):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = ServerError(
        503,
        {
            "error": {
                "code": 503,
                "message": "This model is currently experiencing high demand.",
                "status": "UNAVAILABLE",
            }
        },
        None,
    )
    mock_genai_client.return_value = mock_client

    with pytest.raises(ServiceUnavailableError):
        call_gemini_json(
            api_key="dummy_key",
            system_prompt="prompt",
            payload={"raw_data": {}},
        )

    assert mock_client.models.generate_content.call_count == 1


@patch("services.llm.genai.Client")
def test_call_gemini_json_does_not_outer_retry_on_504(mock_genai_client):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = ServerError(
        504,
        {
            "error": {
                "code": 504,
                "message": "Deadline expired before operation could complete.",
                "status": "DEADLINE_EXCEEDED",
            }
        },
        None,
    )
    mock_genai_client.return_value = mock_client

    with pytest.raises(ServiceUnavailableError):
        call_gemini_json(
            api_key="dummy_key",
            system_prompt="prompt",
            payload={"raw_data": {}},
        )

    assert mock_client.models.generate_content.call_count == 1


@patch("services.llm.genai.Client")
def test_call_gemini_json_does_not_outer_retry_on_deadline_exceeded_text(mock_genai_client):
    class FakeDeadlineError(Exception):
        pass

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = FakeDeadlineError(
        "504 DEADLINE_EXCEEDED. {'error': {'code': 504, 'status': 'DEADLINE_EXCEEDED'}}"
    )
    mock_genai_client.return_value = mock_client

    with pytest.raises(ServiceUnavailableError):
        call_gemini_json(
            api_key="dummy_key",
            system_prompt="prompt",
            payload={"raw_data": {}},
        )

    assert mock_client.models.generate_content.call_count == 1
