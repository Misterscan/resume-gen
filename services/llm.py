import json
import logging
from typing import Any, Dict, Optional
from google import genai
from google.genai import types
import pydantic

from prompts import (
    RESUME_SYSTEM_PROMPT,
    REVISION_SYSTEM_PROMPT,
    COVER_LETTER_SYSTEM_PROMPT,
)
from .schemas import ResumeSchema, RevisionResponseSchema, CoverLetterSchema
from .exceptions import IntegrationError, ValidationError

logger = logging.getLogger(__name__)
MODEL_NAME = "gemini-3.1-pro-preview"

def call_gemini_json(
    api_key: str,
    system_prompt: str,
    payload: Dict[str, Any],
    temperature: float = 0.3,
) -> Dict[str, Any]:
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=json.dumps(payload, indent=2),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                response_mime_type="application/json",
            ),
        )

        response_text = response.text
        if not response_text:
            raise IntegrationError("Gemini returned an empty response.")
            
        import re
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
            response_text = match.group(0)

        try:
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                raise IntegrationError("Gemini returned JSON, but not a JSON object.")
        except json.JSONDecodeError as exc:
            logger.error(f"Failed to parse JSON from Gemini. Raw response:\n{response_text}")
            raise IntegrationError(f"JSON parse error: {exc}")

        return parsed

    except Exception as exc:
        logger.error(f"Gemini generation failed: {exc}")
        raise IntegrationError(f"Gemini API error: {exc}")

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

    result = call_gemini_json(
        api_key=api_key,
        system_prompt=REVISION_SYSTEM_PROMPT,
        payload=payload,
        temperature=0.2,
    )

    try:
        validated = RevisionResponseSchema.model_validate(result)
        out = validated.model_dump()
        if isinstance(out.get('candidate', {}).get('contact_info'), dict):
            out['candidate']['contact_info'] = " | ".join(str(v) for v in out['candidate']['contact_info'].values())
        return out
    except pydantic.ValidationError as e:
        logger.error(f"Revision schema validation failed:\n{e}")
        raise ValidationError(f"Revision schema validation: {e}")

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
