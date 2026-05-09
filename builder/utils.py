import os
import io
import zipfile
from typing import Any, Dict, Optional

from services.document import (
        extract_docx_text,
        generate_docx_stream,
        generate_cover_letter_docx_stream,
        generate_pdf_stream,
        generate_cover_letter_pdf_stream,
    )
from services.workflow import ResumeWorkflow, ResumeWorkflowInput, ResumeWorkflowResult
from services.filenames import sanitize_filename


def require_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return api_key


def run_resume_workflow(raw_data: dict, options: dict) -> ResumeWorkflowResult:
    workflow = ResumeWorkflow()
    payload = ResumeWorkflowInput(
        raw_data=options.get("cached_raw_data") or raw_data,
        api_key=require_api_key(),
        revision_notes=options.get("revision_notes", ""),
        resume_text=options.get("gdrive_text", ""),
        current_resume=options.get("cached_resume"),
        target_role=options.get("target_role", ""),
        target_company=options.get("target_company", ""),
        job_description=options.get("job_description", ""),
    )

    if options.get("mode") == "ats_fix":
        if not options.get("cached_resume"):
            raise RuntimeError("ATS fix requires a cached ATS baseline resume.")
        result = workflow.revise(payload)
    elif options.get("cached_resume"):
        result = ResumeWorkflowResult(
            raw_data=payload.raw_data,
            resume=options["cached_resume"],
            cover_letter=options.get("cached_cover_letter"),
        )
    elif options.get("mode") == "revise":
        if options.get("uploaded_file") and not payload.resume_text:
            from services.document import extract_docx_text
            payload = ResumeWorkflowInput(
                **{**payload.__dict__, "resume_text": extract_docx_text(options["uploaded_file"])}
            )
        result = workflow.revise(payload)
    else:
        result = workflow.generate(payload)

    if options.get("generate_cl") and not result.cover_letter:
        result = workflow.add_cover_letter(result, payload)

    return result


def safe_basename(candidate_data: Dict[str, Any]) -> str:
    full_name = str(candidate_data.get("full_name") or "My Resume")
    return sanitize_filename(full_name).removesuffix(".docx").removesuffix(".pdf")


def render_output(result: ResumeWorkflowResult, output_format: str):
    base_name = safe_basename(result.raw_data)
    generated_files: list[tuple[io.BytesIO, str]] = []

    if output_format in {"docx", "both"}:
        generated_files.append(
            (
                generate_docx_stream(result.resume, result.raw_data),
                f"{base_name}.docx",
            )
        )

        if result.cover_letter:
            generated_files.append(
                (
                    generate_cover_letter_docx_stream(
                        result.cover_letter,
                        result.raw_data,
                    ),
                    f"{base_name} Cover Letter.docx",
                )
            )

    if output_format in {"pdf", "both"}:
        generated_files.append(
            (
                generate_pdf_stream(result.resume, result.raw_data),
                f"{base_name}.pdf",
            )
        )

        if result.cover_letter:
            generated_files.append(
                (
                    generate_cover_letter_pdf_stream(
                        result.cover_letter,
                        result.raw_data,
                    ),
                    f"{base_name} Cover Letter.pdf",
                )
            )

    if not generated_files:
        raise RuntimeError("No output files were generated.")

    if len(generated_files) == 1:
        stream, filename = generated_files[0]
        return stream, filename, result.resume, result.cover_letter, result.raw_data

    zip_stream = io.BytesIO()

    with zipfile.ZipFile(zip_stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for stream, filename in generated_files:
            archive.writestr(filename, stream.getvalue())

    zip_stream.seek(0)

    return (
        zip_stream,
        f"{base_name}.zip",
        result.resume,
        result.cover_letter,
        result.raw_data,
    )


def build_resume_from_payload(raw_data: dict, options: dict):
    result = run_resume_workflow(raw_data, options)
    return render_output(result, options.get("format", "pdf"))