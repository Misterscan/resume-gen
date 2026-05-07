import os
import io
import sys
import tempfile
import zipfile
from pathlib import Path
import logging

try:
    from services import (
        generate_resume_content,
        revise_resume_content,
        extract_docx_text,
        generate_cover_letter_content,
        generate_docx_stream,
        generate_cover_letter_docx_stream,
        generate_pdf_stream,
        generate_cover_letter_pdf_stream,
    )
except ImportError as e:
    logging.getLogger(__name__).error(f"Failed to import services logic: {str(e)}")

def build_resume_from_payload(raw_data: dict, options: dict):
    """
    Acts as a bridge between the Django web form and the existing `main.py`
    core logic by manually gathering the API context and executing the script
    silently without STDIN prompts, saving output to a temporary system dir.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set. Please set it in your environment.")
        
    uploaded_file = options.get("uploaded_file")
    revision_notes = options.get("revision_notes", "")
    
    resume = options.get("cached_resume")
    cover_letter = options.get("cached_cover_letter")
    if options.get("cached_raw_data"):
        raw_data = options["cached_raw_data"]

    if not resume:
        if options.get("mode") == "revise":
            if uploaded_file or options.get("gdrive_text"):
                # Revise existing docx or gdoc
                resume_text = options.get("gdrive_text")
                if not resume_text:
                    resume_text = extract_docx_text(uploaded_file)
                revised = revise_resume_content(
                    api_key=api_key,
                    revision_notes=revision_notes,
                    resume_text=resume_text,
                )
                raw_data = revised["candidate"]
                resume = revised["resume"]
                raw_data["source_resume_text"] = resume_text
            else:
                # Revise from manual web form fields
                revised = revise_resume_content(
                    api_key=api_key,
                    revision_notes=revision_notes,
                    raw_data=raw_data,
                )
                raw_data = revised["candidate"]
                resume = revised["resume"]
        else:
            # Generate structured JSON resume content from raw data
            resume = generate_resume_content(raw_data, api_key)
            
        # Check if we generate Cover Letter
        if options.get("generate_cl"):
            cover_letter = generate_cover_letter_content(
                raw_data=raw_data,
                resume=resume,
                revision_notes=revision_notes,
                target_role=options.get("target_role", ""),
                target_company=options.get("target_company", ""),
                api_key=api_key,
            )
    
    base_name = raw_data.get("full_name", "My Resume").replace("/", "_").replace(" ", "_")
    output_format = options.get("format", "pdf")
    
    generated_files = []
    
    if output_format in ('docx', 'both'):
        stream = generate_docx_stream(resume, raw_data)
        generated_files.append((stream, f"{base_name}.docx"))
        
        if cover_letter:
            cl_stream = generate_cover_letter_docx_stream(cover_letter, raw_data)
            generated_files.append((cl_stream, f"{base_name} Cover Letter.docx"))
            
    if output_format in ('pdf', 'both'):
        stream = generate_pdf_stream(resume, raw_data)
        generated_files.append((stream, f"{base_name}.pdf"))
        
        if cover_letter:
            cl_stream = generate_cover_letter_pdf_stream(cover_letter, raw_data)
            generated_files.append((cl_stream, f"{base_name} Cover Letter.pdf"))
            
    if len(generated_files) == 1:
        return generated_files[0][0], generated_files[0][1], resume, cover_letter, raw_data
        
    zip_stream = io.BytesIO()
    with zipfile.ZipFile(zip_stream, 'w') as zf:
        for stream, name in generated_files:
            zf.writestr(name, stream.getvalue())
    zip_stream.seek(0)
    return zip_stream, f"{base_name}.zip", resume, cover_letter, raw_data