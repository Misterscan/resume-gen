from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .llm import (
    generate_resume_content,
    revise_resume_content,
    generate_cover_letter_content,
    verify_ats_compatibility,
)


@dataclass(frozen=True)
class ResumeWorkflowInput:
    raw_data: Dict[str, Any]
    api_key: str
    revision_notes: str = ""
    resume_text: str = ""
    current_resume: Optional[Dict[str, Any]] = None
    target_role: str = ""
    target_company: str = ""
    job_description: str = ""


@dataclass(frozen=True)
class ResumeWorkflowResult:
    raw_data: Dict[str, Any]
    resume: Dict[str, Any]
    cover_letter: Optional[Dict[str, Any]] = None
    ats_report: Optional[Dict[str, Any]] = None


class ResumeWorkflow:
    def generate(self, payload: ResumeWorkflowInput) -> ResumeWorkflowResult:
        resume = generate_resume_content(payload.raw_data, payload.api_key)
        return ResumeWorkflowResult(raw_data=payload.raw_data, resume=resume)

    def revise(self, payload: ResumeWorkflowInput) -> ResumeWorkflowResult:
        revised = revise_resume_content(
            api_key=payload.api_key,
            revision_notes=payload.revision_notes,
            raw_data=payload.raw_data,
            current_resume=payload.current_resume,
            resume_text=payload.resume_text or None,
        )
        return ResumeWorkflowResult(
            raw_data=revised["candidate"],
            resume=revised["resume"],
        )

    def add_cover_letter(
        self,
        result: ResumeWorkflowResult,
        payload: ResumeWorkflowInput,
    ) -> ResumeWorkflowResult:
        cover_letter = generate_cover_letter_content(
            raw_data=result.raw_data,
            resume=result.resume,
            revision_notes=payload.revision_notes,
            target_role=payload.target_role,
            target_company=payload.target_company,
            api_key=payload.api_key,
        )
        return ResumeWorkflowResult(
            raw_data=result.raw_data,
            resume=result.resume,
            cover_letter=cover_letter,
            ats_report=result.ats_report,
        )

    def add_ats_report(
        self,
        result: ResumeWorkflowResult,
        payload: ResumeWorkflowInput,
    ) -> ResumeWorkflowResult:
        report = verify_ats_compatibility(
            resume=result.resume,
            target_role=payload.target_role,
            job_description=payload.job_description,
            api_key=payload.api_key,
        )
        return ResumeWorkflowResult(
            raw_data=result.raw_data,
            resume=result.resume,
            cover_letter=result.cover_letter,
            ats_report=report,
        )