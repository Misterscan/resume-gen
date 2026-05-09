import json
from unittest.mock import patch

from django.test import RequestFactory
from builder.views import compute_resume_cache_hash, generate, parse_resume_form, verify_ats
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


def test_parse_resume_form_no_work_experience_keeps_alternative_text():
    request = RequestFactory().post(
        "/generate/",
        data={
            "full_name": "Jane Doe",
            "contact_info": "jane@example.com",
            "summary": "Entry-level developer",
            "no_work_experience": "yes",
            "alternative_experience_text": "Built a capstone app and volunteered as coding tutor.",
            "skills": "Python, Django",
            "format": "pdf",
            "mode": "scratch",
            "exp_title[]": [""],
            "exp_company[]": [""],
            "exp_location[]": [""],
            "exp_dates[]": [""],
            "exp_bullets[]": [""],
            "edu_inst[]": ["State University"],
            "edu_credential_type[]": ["GED"],
            "edu_deg[]": [""],
            "edu_loc[]": ["Remote"],
            "edu_dates[]": ["2022 - 2026"],
            "edu_details[]": ["Capstone in ML"],
        },
    )

    raw_data, options, _ = parse_resume_form(request)

    assert raw_data["no_work_experience"] is True
    assert raw_data["alternative_experience_text"] == "Built a capstone app and volunteered as coding tutor."
    assert raw_data["work_experience"] == []
    assert raw_data["education"][0]["degree"] == "GED"
    assert options["mode"] == "scratch"


def test_parse_resume_form_with_work_experience_ignores_alternative_text_requirement():
    request = RequestFactory().post(
        "/generate/",
        data={
            "full_name": "Jane Doe",
            "contact_info": "jane@example.com",
            "summary": "Software engineer",
            "no_work_experience": "no",
            "alternative_experience_text": "",
            "skills": "Python, Django",
            "format": "pdf",
            "mode": "scratch",
            "exp_title[]": ["Engineer"],
            "exp_company[]": ["Acme"],
            "exp_location[]": ["Remote"],
            "exp_dates[]": ["2024 - Present"],
            "exp_bullets[]": ["Built APIs"],
            "edu_inst[]": ["State University"],
            "edu_credential_type[]": ["Bachelor's Degree"],
            "edu_deg[]": ["B.S. Computer Science"],
            "edu_loc[]": ["Remote"],
            "edu_dates[]": ["2022 - 2026"],
            "edu_details[]": ["Capstone in ML"],
        },
    )

    raw_data, _, _ = parse_resume_form(request)

    assert raw_data["no_work_experience"] is False
    assert len(raw_data["work_experience"]) == 1
    assert raw_data["work_experience"][0]["title"] == "Engineer"
    assert raw_data["education"][0]["degree"] == "B.S. Computer Science"
    assert "Education type: Bachelor's Degree" in raw_data["education"][0]["details"]


@patch("builder.views.build_resume_from_payload")
@patch("builder.views.parse_resume_form")
def test_generate_ats_fix_requires_cached_ats_baseline(mock_parse_resume_form, mock_build_resume):
    mock_parse_resume_form.return_value = (
        {"full_name": "Jane Doe"},
        {
            "mode": "ats_fix",
            "generate_cl": False,
            "target_role": "",
            "target_company": "",
            "revision_notes": "Apply ATS suggestions",
            "format": "pdf",
        },
        b"",
    )

    request = RequestFactory().post("/generate/", data={"ats_source_hash": "missing-hash"})
    request.session = {}

    response = generate(request)

    assert response.status_code == 400
    assert b"Run ATS check first" in response.content
    mock_build_resume.assert_not_called()


@patch("builder.views.submit_job")
@patch("builder.views.parse_resume_form")
def test_verify_ats_returns_resume_hash_for_fix_flow(mock_parse_resume_form, mock_submit_job):
    mock_parse_resume_form.return_value = (
        {"full_name": "Jane Doe", "summary": "Engineer"},
        {
            "mode": "scratch",
            "generate_cl": False,
            "target_role": "Backend Engineer",
            "target_company": "Acme",
            "job_description": "Build backend services",
            "revision_notes": "",
        },
        b"",
    )
    mock_submit_job.return_value = "job-123"

    request = RequestFactory().post("/verify-ats/")
    request.session = {}

    with patch("builder.views.os.environ.get", return_value="test-key"):
        response = verify_ats(request)

    data = json.loads(response.content.decode("utf-8"))
    expected_hash = compute_resume_cache_hash(
        {"full_name": "Jane Doe", "summary": "Engineer"},
        {
            "mode": "scratch",
            "generate_cl": False,
            "target_role": "Backend Engineer",
            "target_company": "Acme",
            "job_description": "Build backend services",
            "revision_notes": "",
        },
        b"",
    )

    assert response.status_code == 200
    assert data["job_id"] == "job-123"
    assert data["resume_hash"] == expected_hash
    assert request.session.get("last_resume_hash") == expected_hash