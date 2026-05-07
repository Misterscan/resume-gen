import pytest
from django.core.exceptions import SuspiciousOperation
from django.core.files.uploadedfile import SimpleUploadedFile

from services.filenames import sanitize_filename
from builder.views import validate_docx_upload
from services.schemas import AtsVerificationSchema


def test_sanitize_filename_removes_path_segments():
    assert sanitize_filename("../../secret.docx") == "secret.docx"


def test_sanitize_filename_removes_control_and_script_chars():
    assert "<script>" not in sanitize_filename("<script>alert(1)</script>.docx")


def test_rejects_non_docx_upload_by_extension():
    upload = SimpleUploadedFile(
        "resume.pdf",
        b"%PDF-1.7",
        content_type="application/pdf",
    )

    with pytest.raises(SuspiciousOperation):
        validate_docx_upload(upload)


def test_rejects_oversized_upload():
    upload = SimpleUploadedFile(
        "resume.docx",
        b"x" * (5 * 1024 * 1024 + 1),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    with pytest.raises(SuspiciousOperation):
        validate_docx_upload(upload)


from pydantic import ValidationError

def test_ats_score_must_be_between_zero_and_100():
    with pytest.raises(ValidationError):
        AtsVerificationSchema.model_validate(
            {
                "ats_score": 101,
                "keyword_match_rate": "100%",
                "missing_keywords": [],
                "formatting_feedback": [],
                "content_feedback": [],
                "overall_recommendation": "Invalid score.",
            }
        )