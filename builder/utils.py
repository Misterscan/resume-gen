import os
import sys
import tempfile
import zipfile
from pathlib import Path
import logging

try:
    # We dynamically insert the parent directory into PYTHONPATH so we can import `main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from main import (
        generate_resume_content, 
        revise_resume_content, 
        extract_docx_text, 
        generate_cover_letter_content, 
        save_as_pdf, 
        save_as_docx, 
        save_cover_letter_as_docx, 
        save_cover_letter_as_pdf
    )
except ImportError as e:
    logging.getLogger(__name__).error(f"Failed to import main.py logic: {str(e)}")

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
                    resume_text = extract_docx_text(Path(uploaded_file))
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
    
    # Store it in a securely bounded tmp directory for Django to stream back
    tmp_folder = tempfile.mkdtemp()
    base_name = raw_data.get("full_name", "My Resume").replace("/", "_").replace(" ", "_")
    output_format = options.get("format", "pdf")
    
    generated_files = []
    
    if output_format in ('docx', 'both'):
        out_path = Path(tmp_folder) / f"{base_name}.docx"
        save_as_docx(resume, raw_data, out_path)
        generated_files.append((out_path, f"{base_name}.docx"))
        
        if cover_letter:
            cl_path = Path(tmp_folder) / f"{base_name} Cover Letter.docx"
            save_cover_letter_as_docx(cover_letter, raw_data, cl_path)
            generated_files.append((cl_path, f"{base_name} Cover Letter.docx"))
            
    if output_format in ('pdf', 'both'):
        out_path = Path(tmp_folder) / f"{base_name}.pdf"
        save_as_pdf(resume, raw_data, out_path)
        generated_files.append((out_path, f"{base_name}.pdf"))
        
        if cover_letter:
            cl_path = Path(tmp_folder) / f"{base_name} Cover Letter.pdf"
            save_cover_letter_as_pdf(cover_letter, raw_data, cl_path)
            generated_files.append((cl_path, f"{base_name} Cover Letter.pdf"))
            
    if len(generated_files) == 1:
        return str(generated_files[0][0]), generated_files[0][1], resume, cover_letter, raw_data
        
    # multiple files -> zip
    zip_path = Path(tmp_folder) / f"{base_name}.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for path, name in generated_files:
            zf.write(path, name)
            
    return str(zip_path), f"{base_name}.zip", resume, cover_letter, raw_data