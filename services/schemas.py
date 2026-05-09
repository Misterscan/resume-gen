from typing import Any, Dict, List, Union

from pydantic import BaseModel, Field, field_validator


def _clean_text(value: Any, *, max_length: int) -> str:
    text = "" if value is None else str(value).strip()
    return text[:max_length]


def _clean_text_list(values: Any, *, max_items: int, max_length: int) -> List[str]:
    if values is None:
        return []

    if not isinstance(values, list):
        values = [values]

    cleaned: List[str] = []
    for value in values[:max_items]:
        text = _clean_text(value, max_length=max_length)
        if text:
            cleaned.append(text)

    return cleaned


class StrictBaseModel(BaseModel):
    model_config = {
        "extra": "forbid",
        "str_strip_whitespace": True,
    }


class WorkExperience(StrictBaseModel):
    title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=300)
    bullets: List[str] = Field(default_factory=list, max_length=12)

    @field_validator("title", "company", "location", "dates", mode="before")
    @classmethod
    def clean_short_text(cls, value: Any) -> str:
        return _clean_text(value, max_length=300)

    @field_validator("bullets", mode="before")
    @classmethod
    def clean_bullets(cls, value: Any) -> List[str]:
        return _clean_text_list(value, max_items=12, max_length=2_000)


class ProjectItem(StrictBaseModel):
    name: str = Field(default="", max_length=300)
    organization: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=300)
    bullets: List[str] = Field(default_factory=list, max_length=8)

    @field_validator("name", "organization", "location", "dates", mode="before")
    @classmethod
    def clean_short_text(cls, value: Any) -> str:
        return _clean_text(value, max_length=300)

    @field_validator("bullets", mode="before")
    @classmethod
    def clean_bullets(cls, value: Any) -> List[str]:
        return _clean_text_list(value, max_items=8, max_length=2_000)


class VolunteerExperienceItem(StrictBaseModel):
    role: str = Field(default="", max_length=300)
    organization: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=300)
    bullets: List[str] = Field(default_factory=list, max_length=8)

    @field_validator("role", "organization", "location", "dates", mode="before")
    @classmethod
    def clean_short_text(cls, value: Any) -> str:
        return _clean_text(value, max_length=300)

    @field_validator("bullets", mode="before")
    @classmethod
    def clean_bullets(cls, value: Any) -> List[str]:
        return _clean_text_list(value, max_items=8, max_length=2_000)


class CertificationItem(StrictBaseModel):
    name: str = Field(default="", max_length=300)
    issuer: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=300)
    details: str = Field(default="", max_length=2_000)

    @field_validator("name", "issuer", "dates", mode="before")
    @classmethod
    def clean_short_text(cls, value: Any) -> str:
        return _clean_text(value, max_length=300)

    @field_validator("details", mode="before")
    @classmethod
    def clean_details(cls, value: Any) -> str:
        return _clean_text(value, max_length=2_000)


class EducationItem(StrictBaseModel):
    institution: str = Field(default="", max_length=300)
    degree: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=300)
    details: Union[str, List[str]] = ""

    @field_validator("institution", "degree", "location", "dates", mode="before")
    @classmethod
    def clean_short_text(cls, value: Any) -> str:
        return _clean_text(value, max_length=300)

    @field_validator("details", mode="before")
    @classmethod
    def clean_details(cls, value: Any) -> Union[str, List[str]]:
        if isinstance(value, list):
            return _clean_text_list(value, max_items=10, max_length=2_000)
        return _clean_text(value, max_length=2_000)


class ResumeSchema(StrictBaseModel):
    professional_summary: str = Field(default="", max_length=8_000)
    work_experience: List[WorkExperience] = Field(default_factory=list, max_length=20)
    projects: List[ProjectItem] = Field(default_factory=list, max_length=20)
    volunteer_experience: List[VolunteerExperienceItem] = Field(default_factory=list, max_length=20)
    certifications: List[CertificationItem] = Field(default_factory=list, max_length=20)
    education: List[EducationItem] = Field(default_factory=list, max_length=10)
    skills: List[str] = Field(default_factory=list, max_length=80)

    @field_validator("professional_summary", mode="before")
    @classmethod
    def clean_summary(cls, value: Any) -> str:
        return _clean_text(value, max_length=8_000)

    @field_validator("skills", mode="before")
    @classmethod
    def clean_and_dedupe_skills(cls, value: Any) -> List[str]:
        skills = _clean_text_list(value, max_items=80, max_length=300)

        seen: set[str] = set()
        deduped: List[str] = []

        for skill in skills:
            key = skill.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(skill)

        return deduped


class CandidateSchema(StrictBaseModel):
    full_name: str = Field(default="", max_length=300)
    contact_info: Union[str, Dict[str, Any]] = ""

    @field_validator("full_name", mode="before")
    @classmethod
    def clean_full_name(cls, value: Any) -> str:
        return _clean_text(value, max_length=300)

    @field_validator("contact_info", mode="before")
    @classmethod
    def clean_contact_info(cls, value: Any) -> Union[str, Dict[str, Any]]:
        if isinstance(value, dict):
            return {
                str(key): _clean_text(val, max_length=300)
                for key, val in value.items()
                if _clean_text(val, max_length=300)
            }
        return _clean_text(value, max_length=500)


class RevisionResponseSchema(StrictBaseModel):
    candidate: CandidateSchema
    resume: ResumeSchema


class CoverLetterSchema(StrictBaseModel):
    recipient_info: str = Field(default="", max_length=2_000)
    greeting: str = Field(default="", max_length=300)
    introduction: str = Field(default="", max_length=8_000)
    body_paragraphs: List[str] = Field(default_factory=list, max_length=4)
    company_connection: str = Field(default="", max_length=8_000)
    closing: str = Field(default="", max_length=8_000)
    sign_off: str = Field(default="", max_length=300)

    @field_validator(
        "recipient_info",
        "greeting",
        "introduction",
        "company_connection",
        "closing",
        "sign_off",
        mode="before",
    )
    @classmethod
    def clean_text_fields(cls, value: Any) -> str:
        return _clean_text(value, max_length=8_000)

    @field_validator("body_paragraphs", mode="before")
    @classmethod
    def clean_body_paragraphs(cls, value: Any) -> List[str]:
        return _clean_text_list(value, max_items=4, max_length=8_000)


class AtsVerificationSchema(StrictBaseModel):
    ats_score: int = Field(ge=0, le=100)
    keyword_match_rate: str = Field(default="", max_length=300)
    missing_keywords: List[str] = Field(default_factory=list, max_length=80)
    formatting_feedback: List[str] = Field(default_factory=list, max_length=20)
    content_feedback: List[str] = Field(default_factory=list, max_length=20)
    overall_recommendation: str = Field(default="", max_length=8_000)

    @field_validator("keyword_match_rate", "overall_recommendation", mode="before")
    @classmethod
    def clean_text_fields(cls, value: Any) -> str:
        return _clean_text(value, max_length=8_000)

    @field_validator("missing_keywords", mode="before")
    @classmethod
    def clean_missing_keywords(cls, value: Any) -> List[str]:
        return _clean_text_list(value, max_items=80, max_length=300)

    @field_validator("formatting_feedback", "content_feedback", mode="before")
    @classmethod
    def clean_feedback(cls, value: Any) -> List[str]:
        return _clean_text_list(value, max_items=20, max_length=2_000)