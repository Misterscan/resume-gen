from typing import Any, Dict, List, Union
from pydantic import BaseModel, Field

class WorkExperience(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    dates: str = ""
    bullets: List[str] = Field(default_factory=list)

class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    location: str = ""
    dates: str = ""
    details: Union[str, List[str]] = ""

class ResumeSchema(BaseModel):
    professional_summary: str = ""
    work_experience: List[WorkExperience] = Field(default_factory=list)
    education: List[EducationItem] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)

class CandidateSchema(BaseModel):
    full_name: str = ""
    contact_info: Union[str, Dict[str, Any]] = ""

class RevisionResponseSchema(BaseModel):
    candidate: CandidateSchema
    resume: ResumeSchema

class CoverLetterSchema(BaseModel):
    recipient_info: str = ""
    greeting: str = ""
    introduction: str = ""
    body_paragraphs: List[str] = Field(default_factory=list)
    company_connection: str = ""
    closing: str = ""
    sign_off: str = ""
