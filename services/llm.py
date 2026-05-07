# services/llm.py
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pydantic
from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .exceptions import IntegrationError

from prompts import (
    RESUME_SYSTEM_PROMPT,
    REVISION_SYSTEM_PROMPT,
    COVER_LETTER_SYSTEM_PROMPT,
    ATS_VERIFICATION_SYSTEM_PROMPT,
)
from .schemas import ResumeSchema, RevisionResponseSchema, CoverLetterSchema, AtsVerificationSchema
from .exceptions import IntegrationError, ValidationError

logger = logging.getLogger(__name__)
MODEL_NAME = "gemini-3.1-pro-preview"
MAX_MODEL_PAYLOAD_CHARS = 120_000


@dataclass(frozen=True)
class GeminiRequest:
    api_key: str
    system_prompt: str
    payload: Dict[str, Any]
    temperature: float = 0.3


def _serialize_payload(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_MODEL_PAYLOAD_CHARS:
        raise IntegrationError(
            f"Model payload is too large: {len(text)} characters. "
            "Reduce resume text or job description length."
        )
    return text


@retry(
    retry=retry_if_exception_type(IntegrationError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def call_gemini_json(
    api_key: str,
    system_prompt: str,
    payload: Dict[str, Any],
    temperature: float = 0.3,
) -> Dict[str, Any]:
    body = _serialize_payload(payload)

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=body,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
            ),
        )
    except Exception as exc:
        logger.warning("Gemini request failed: %s", exc.__class__.__name__)
        raise IntegrationError("Gemini request failed.") from exc

    response_text = (response.text or "").strip()
    if not response_text:
        raise IntegrationError("Gemini returned an empty response.")

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Gemini returned invalid JSON. Response length=%s",
            len(response_text),
        )
        raise IntegrationError("Gemini returned invalid JSON.") from exc

    if not isinstance(parsed, dict):
        raise IntegrationError("Gemini returned JSON, but not a JSON object.")

    return parsed

def generate_resume_content(
    raw_data: Dict[str, Any],
    api_key: str,
) -> Dict[str, Any]:
    parsed = call_gemini_json(
        api_key=api_key,
        system_prompt=RESUME_SYSTEM_PROMPT,
        payload={"raw_data": raw_data},
        temperature=0.3,
    )
    try:
        validated = ResumeSchema.model_validate(parsed)
        return validated.model_dump()
    except pydantic.ValidationError as e:
        logger.error(f"Resume schema validation failed:\n{e}")
        raise ValidationError(f"Resume schema validation: {e}")

def revise_resume_content(
    api_key: str,
    revision_notes: str,
    raw_data: Optional[Dict[str, Any]] = None,
    current_resume: Optional[Dict[str, Any]] = None,
    resume_text: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "revision_notes": revision_notes,
        "raw_data": raw_data or {},
        "current_resume": current_resume or {},
        "existing_resume_text": resume_text or "",
    }

    # Fail explicitly instead of silently dropping user history.
    _serialize_payload(payload)

    result = call_gemini_json(
        api_key=api_key,
        system_prompt=REVISION_SYSTEM_PROMPT,
        payload=payload,
        temperature=0.2,
    )

    try:
        validated = RevisionResponseSchema.model_validate(result)
        out = validated.model_dump()
        contact_info = out.get("candidate", {}).get("contact_info")
        if isinstance(contact_info, dict):
            out["candidate"]["contact_info"] = " | ".join(
                str(value) for value in contact_info.values() if value
            )
        return out
    except pydantic.ValidationError as exc:
        logger.info("Revision schema validation failed.")
        raise ValidationError("Revision response did not match the expected schema.") from exc

def generate_cover_letter_content(
    raw_data: Dict[str, Any],
    resume: Dict[str, Any],
    revision_notes: Optional[str],
    target_role: str,
    target_company: str,
    api_key: str,
) -> Dict[str, Any]:
    parsed = call_gemini_json(
        api_key=api_key,
        system_prompt=COVER_LETTER_SYSTEM_PROMPT,
        payload={
            "raw_data": raw_data,
            "resume": resume,
            "revision_notes": revision_notes or "",
            "target_role": target_role,
            "target_company": target_company,
        },
        temperature=0.4,
    )
    
    try:
        validated = CoverLetterSchema.model_validate(parsed)
        return validated.model_dump()
    except pydantic.ValidationError as e:
        logger.error(f"Cover letter schema validation failed:\n{e}")
        raise ValidationError(f"Cover letter schema validation: {e}")

def verify_ats_compatibility(
    resume: Dict[str, Any],
    target_role: str,
    job_description: str,
    api_key: str,
) -> Dict[str, Any]:
    payload = {
        "resume": resume,
        "target_role": target_role,
        "job_description": job_description
    }
    parsed = call_gemini_json(
        api_key=api_key,
        system_prompt=ATS_VERIFICATION_SYSTEM_PROMPT,
        payload=payload,
        temperature=0.1,
    )
    
    try:
        validated = AtsVerificationSchema.model_validate(parsed)
        return validated.model_dump()
    except pydantic.ValidationError as e:
        logger.error(f"ATS verification schema validation failed:\n{e}")
        raise ValidationError(f"ATS verification schema validation: {e}")
