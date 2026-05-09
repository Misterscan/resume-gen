# services/llm.py
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pydantic
from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from prompts import (
    RESUME_SYSTEM_PROMPT,
    RESUME_NO_EXP_SYSTEM_PROMPT,
    REVISION_SYSTEM_PROMPT,
    COVER_LETTER_SYSTEM_PROMPT,
    ATS_VERIFICATION_SYSTEM_PROMPT,
)
from .schemas import ResumeSchema, RevisionResponseSchema, CoverLetterSchema, AtsVerificationSchema
from .exceptions import IntegrationError, ServiceUnavailableError, ValidationError

logger = logging.getLogger(__name__)
MODEL_NAME = "gemini-3.1-pro-preview"
MAX_MODEL_PAYLOAD_CHARS = 120_000
REQUEST_TIMEOUT_MS = 45_000
ATS_REQUEST_TIMEOUT_MS = 120_000


def _is_temporary_upstream_error(exc: Exception) -> bool:
    from google.genai.errors import ServerError

    if isinstance(exc, ServerError):
        code = getattr(exc, "code", None)
        if code in {503, 504}:
            return True

    text = str(exc).upper()
    return "DEADLINE_EXCEEDED" in text or "GATEWAY TIMEOUT" in text


@dataclass(frozen=True)
class GeminiRequest:
    api_key: str
    system_prompt: str
    payload: Dict[str, Any]
    temperature: float = 0.3


def _serialize_payload(payload: Dict[str, Any]) -> str:
    # Use indent=2 because LLMs process formatted JSON much faster than dense minified JSON
    text = json.dumps(payload, indent=2)
    if len(text) > MAX_MODEL_PAYLOAD_CHARS:
        # Fallback to minified if too large
        text = json.dumps(payload, separators=(",", ":"))
        if len(text) > MAX_MODEL_PAYLOAD_CHARS:
            raise IntegrationError(
                f"Model payload is too large: {len(text)} characters. "
                "Reduce resume text or job description length."
            )
    return text


@retry(
    retry=retry_if_exception(
        lambda exc: isinstance(exc, IntegrationError)
        and not isinstance(exc, ServiceUnavailableError)
    ),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=2, max=10),
    reraise=True,
)
def call_gemini_json(
    api_key: str,
    system_prompt: str,
    payload: Dict[str, Any],
    temperature: float = 0.3,
    request_timeout_ms: int = REQUEST_TIMEOUT_MS,
) -> Dict[str, Any]:
    body = _serialize_payload(payload)

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=request_timeout_ms),
        )
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
        if _is_temporary_upstream_error(exc):
            logger.warning("Gemini API temporarily unavailable: %s", exc)
            raise ServiceUnavailableError(
                "Gemini API is temporarily unavailable right now. Please try again in a minute."
            ) from exc
        
        # httpx raises TimeoutException on socket timeout
        if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower():
            logger.warning("Gemini request timed out: %s", exc.__class__.__name__)
            raise ServiceUnavailableError(
                "Gemini API timed out. Please try again in a moment."
            ) from exc

        logger.warning(f"Gemini request failed: {exc.__class__.__name__}: {str(exc)}")
        raise IntegrationError(f"Gemini request failed: {str(exc)}") from exc

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
    # Use a dedicated prompt if no_work_experience is set
    system_prompt = RESUME_NO_EXP_SYSTEM_PROMPT if raw_data.get("no_work_experience") else RESUME_SYSTEM_PROMPT
    parsed = call_gemini_json(
        api_key=api_key,
        system_prompt=system_prompt,
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
    truncated_resume = None
    if current_resume:
        truncated_resume = dict(current_resume)
        if "work_experience" in truncated_resume and isinstance(truncated_resume["work_experience"], list):
            if len(truncated_resume["work_experience"]) > 5:
                logger.info("Truncating current resume to save tokens.")
                truncated_resume["work_experience"] = truncated_resume["work_experience"][:5]

    payload: Dict[str, Any] = {
        "revision_notes": revision_notes,
        "raw_data": raw_data or {},
        "current_resume": truncated_resume or {},
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
        "job_description": job_description,
    }
    parsed = call_gemini_json(
        api_key=api_key,
        system_prompt=ATS_VERIFICATION_SYSTEM_PROMPT,
        payload=payload,
        temperature=0.1,
        request_timeout_ms=ATS_REQUEST_TIMEOUT_MS,
    )
    
    try:
        validated = AtsVerificationSchema.model_validate(parsed)
        return validated.model_dump()
    except pydantic.ValidationError as e:
        logger.error(f"ATS verification schema validation failed:\n{e}")
        raise ValidationError(f"ATS verification schema validation: {e}")
