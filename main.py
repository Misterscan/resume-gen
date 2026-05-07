from services import (
    generate_resume_content,
    revise_resume_content,
    generate_cover_letter_content,
    extract_docx_text,
    generate_docx_stream,
    generate_cover_letter_docx_stream,
    generate_pdf_stream,
    generate_cover_letter_pdf_stream,
    get_google_credentials,
    get_gdoc_text,
    upload_to_gdoc,
)

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import io

from prompts import (
    RESUME_SYSTEM_PROMPT,
    REVISION_SYSTEM_PROMPT,
    COVER_LETTER_SYSTEM_PROMPT,
)

SCOPES = ['https://www.googleapis.com/auth/drive.file']

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.1-pro-preview"

# --- Models removed to services/ ---

# --- PDF Configuration ---



def require_api_key() -> str:
    try:
        api_key = os.environ["GEMINI_API_KEY"]
    except KeyError:
        logger.error("ERROR: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)

    if not api_key.strip():
        logger.error("ERROR: GEMINI_API_KEY is empty.")
        sys.exit(1)

    logger.warning("SECURITY NOTE: Ensure your API key is not hardcoded or exposed in cleartext.")

    return api_key

# Google API methods removed to services/


def prompt_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("Required field.")


def prompt_optional(label: str) -> str:
    return input(f"{label}: ").strip()


def prompt_yes_no(label: str, default: str = "n") -> bool:
    valid_defaults = {"y", "n"}
    if default not in valid_defaults:
        default = "n"

    suffix = "[Y/n]" if default == "y" else "[y/N]"
    while True:
        value = input(f"{label} {suffix}: ").strip().lower()
        if not value:
            value = default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter y or n.")


def collect_bullets() -> List[str]:
    bullets = []
    print("Enter bullets/responsibilities. Leave blank when done.")
    while True:
        bullet = input("Bullet: ").strip()
        if not bullet:
            break
        bullets.append(bullet)
    return bullets


def collect_revision_notes() -> str:
    print("\nRevision Data")
    print("Enter missing data, corrections, target role, metrics, keywords, or notes.")
    print("Leave blank when done.")

    notes = []
    while True:
        note = input("Revision note: ").strip()
        if not note:
            break
        notes.append(note)

    return "\n".join(notes)


# removed


def get_user_input() -> Dict[str, Any]:
    print("Resume Data Collection")

    data: Dict[str, Any] = {
        "full_name": prompt_required("Full Name"),
        "contact_info": prompt_required("Contact Info (Email, Phone, LinkedIn)"),
        "summary": prompt_required(
            "Summary (1-2 sentences highlighting your experience and goals)"
        ),
        "work_experience": [],
        "education": [],
        "skills": [],
    }

    print("\nWork Experience")
    while True:
        add_item = input("Add work experience? [y/n]: ").strip().lower()
        if add_item != "y":
            break

        data["work_experience"].append(
            {
                "title": prompt_required("Title"),
                "company": prompt_required("Company"),
                "location": prompt_required("Location"),
                "dates": prompt_required("Dates"),
                "bullets": collect_bullets(),
            }
        )

    print("\nEducation")
    while True:
        add_item = input("Add education? [y/n]: ").strip().lower()
        if add_item != "y":
            break

        data["education"].append(
            {
                "institution": prompt_required("Institution"),
                "degree": prompt_required("Degree"),
                "dates": prompt_optional("Dates"),
                "location": prompt_required("Location"),
                "details": prompt_optional("Details"),
            }
        )

    print("\nSkills")
    skills_raw = prompt_required("Skills, comma-separated")
    data["skills"] = [
        skill.strip() for skill in skills_raw.split(",") if skill.strip()
    ]

    return data


# --- Call gemini json moved to services/ ---


def get_base_path(output_arg: Optional[str], full_name: str, custom_dir: Optional[str] = None) -> Path:
    if output_arg:
        target = output_arg
    elif full_name:
        target = f"{full_name} Resume"
    else:
        target = "My Resume"
    
    target = target.replace("/", "_").replace("\\", "_")
    output_path = Path(target).expanduser().resolve()
    
    if custom_dir:
        output_folder = Path(custom_dir).expanduser().resolve()
    else:
        output_folder = output_path.parent / "resumes"
        
    output_folder.mkdir(parents=True, exist_ok=True)
    return output_folder / output_path.stem

def save_stream_to_disk(stream, path: Path):
    with open(path, "wb") as f:
        f.write(stream.getvalue())

def save_resume_files(
    resume: Dict[str, Any],
    raw_data: Dict[str, Any],
    base_path: Path,
    output_format: str,
    args: Optional[argparse.Namespace] = None,
) -> None:
    if output_format in {"docx", "both", "gdocs"}:
        docx_path = base_path.with_suffix(".docx")
        stream = generate_docx_stream(resume, raw_data)
        save_stream_to_disk(stream, docx_path)
        
        if output_format == "gdocs" and args:
            creds = get_google_credentials()
            overwrite_id = args.gdoc_id if getattr(args, "gdoc_update", False) else None
            link = upload_to_gdoc(str(docx_path), docx_path.name, creds, overwrite_id)
            logger.info(f"Resume Google Doc saved: {link}")
            try:
                os.remove(docx_path)
            except OSError:
                pass


    if output_format in {"pdf", "both"}:
        stream = generate_pdf_stream(resume, raw_data)
        save_stream_to_disk(stream, base_path.with_suffix(".pdf"))


def save_cover_letter_files(
    cover_letter: Dict[str, Any],
    raw_data: Dict[str, Any],
    base_path: Path,
    output_format: str,
    args: Optional[argparse.Namespace] = None,
) -> None:
    cl_base_path = base_path.with_name(f"{base_path.name} Cover Letter")

    if output_format in {"docx", "both", "gdocs"}:
        docx_path = cl_base_path.with_suffix(".docx")
        stream = generate_cover_letter_docx_stream(cover_letter, raw_data)
        save_stream_to_disk(stream, docx_path)
        
        if output_format == "gdocs" and args:
            creds = get_google_credentials()
            link = upload_to_gdoc(str(docx_path), docx_path.name, creds, None) # cover letter never overwrites existing resume
            logger.info(f"Cover Letter Google Doc saved: {link}")
            try:
                os.remove(docx_path)
            except OSError:
                pass


    if output_format in {"pdf", "both"}:
        stream = generate_cover_letter_pdf_stream(cover_letter, raw_data)
        save_stream_to_disk(stream, cl_base_path.with_suffix(".pdf"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an ATS-optimized resume and optional cover letter with Gemini."
    )
    parser.add_argument(
        "--format",
        choices=["pdf", "docx", "both", "gdocs"],
        default="both",
        help="Output format. 'gdocs' will create a Google Doc in your Drive.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Target base filename without extension. Defaults to '<Full Name> Resume'.",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Target output directory. Defaults to './resumes'.",
    )
    parser.add_argument(
        "--revise",
        action="store_true",
        help="Prompt for missing data or corrections after initial resume generation.",
    )
    parser.add_argument(
        "--cl",
        action="store_true",
        help="Automatically generate a tailored cover letter after resume creation or revision.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Existing .docx resume to revise instead of starting from scratch.",
    )
    parser.add_argument(
        "--gdoc-id",
        help="Existing Google Doc ID to revise instead of a local file.",
    )
    parser.add_argument(
        "--gdoc-update",
        action="store_true",
        help="Update the target Google Doc when using --gdoc-id instead of creating a new file.",
    )
    parser.add_argument(
        "--notes",
        help="Revision instructions for --input mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated JSON to the console instead of saving files.",
    )
    return parser.parse_args()



def setup_environment(output_dir: Optional[str]) -> str:
    """Verifies API key and ensures the target output directory exists."""
    api_key = require_api_key()
    
    if output_dir:
        out_path = Path(output_dir).expanduser().resolve()
    else:
        out_path = Path.cwd() / "resumes"
        
    out_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Target output directory verified: {out_path}")
    
    return api_key

def print_diff_summary(old: Dict[str, Any], new: Dict[str, Any]) -> None:
    logger.info("--- Resume Revision Diff Summary ---")
    if old.get("professional_summary") != new.get("professional_summary"):
        logger.info("* Professional Summary was updated.")
        
    old_exp = old.get("work_experience", [])
    new_exp = new.get("work_experience", [])
    if len(old_exp) != len(new_exp):
        logger.info(f"* Work experience count changed: {len(old_exp)} -> {len(new_exp)}")
    else:
        for i, (o, n) in enumerate(zip(old_exp, new_exp)):
            if o != n:
                logger.info(f"* Work experience '{n.get('title')} at {n.get('company')}' was updated.")
                
    old_edu = old.get("education", [])
    new_edu = new.get("education", [])
    if old_edu != new_edu:
        logger.info("* Education section was updated.")
        
    old_skills = set(old.get("skills", []))
    new_skills = set(new.get("skills", []))
    if old_skills != new_skills:
        added = new_skills - old_skills
        removed = old_skills - new_skills
        if added: logger.info(f"* Skills Added: {', '.join(added)}")
        if removed: logger.info(f"* Skills Removed: {', '.join(removed)}")
    logger.info("------------------------------------")


def main() -> None:
    args = parse_args()
    api_key = setup_environment(args.dir)

    if args.input or args.gdoc_id:
        if args.gdoc_id:
            creds = get_google_credentials()
            resume_text = get_gdoc_text(args.gdoc_id, creds)
            
            revision_notes = args.notes or collect_revision_notes()
            if not revision_notes:
                logger.error("ERROR: Revision notes are required for revision.")
                sys.exit(1)
        else:
            input_path = args.input.expanduser().resolve()
            if input_path.suffix.lower() != ".docx":
                logger.error("ERROR: --input currently supports .docx files only.")
                sys.exit(1)
            if not input_path.exists():
                logger.error(f"ERROR: Input file not found: {input_path}")
                sys.exit(1)

            resume_text = extract_docx_text(input_path)
            revision_notes = args.notes or collect_revision_notes()
            if not revision_notes:
                logger.error("ERROR: Revision notes are required in --input mode.")
                sys.exit(1)

        revised = revise_resume_content(
            api_key=api_key,
            revision_notes=revision_notes,
            resume_text=resume_text,
        )
        raw_data = revised["candidate"]
        resume = revised["resume"]
        raw_data["source_resume_text"] = resume_text

        if args.dry_run:
            logger.info("DRY RUN: Outputting Revised Resume JSON:")
            print(json.dumps(resume, indent=2))
        else:
            base_path = get_base_path(args.output, raw_data.get("full_name", ""), args.dir)
            save_resume_files(resume, raw_data, base_path, args.format, args)

        should_generate_cl = args.cl or prompt_yes_no(
            "Generate a tailored Cover Letter from the revised resume?",
            default="n",
        )
        if should_generate_cl:
            target_role = prompt_optional("Target Role / Job Title")
            target_company = prompt_optional("Target Company")
            cover_letter = generate_cover_letter_content(
                raw_data=raw_data,
                resume=resume,
                revision_notes=revision_notes,
                target_role=target_role,
                target_company=target_company,
                api_key=api_key,
            )
            if args.dry_run:
                logger.info("DRY RUN: Outputting Cover Letter JSON:")
                print(json.dumps(cover_letter, indent=2))
            else:
                save_cover_letter_files(cover_letter, raw_data, base_path, args.format, args)

        print("Generation complete.")
        return

    raw_data = get_user_input()
    resume = generate_resume_content(raw_data, api_key)

    revision_notes = ""
    should_revise = args.revise or prompt_yes_no(
        "Revise resume with missing data or corrections?",
        default="n",
    )

    if should_revise:
        revision_notes = collect_revision_notes()
        if revision_notes:
            revised = revise_resume_content(
                api_key=api_key,
                revision_notes=revision_notes,
                raw_data=raw_data,
                current_resume=resume,
            )
            candidate = revised["candidate"]
            raw_data["full_name"] = candidate.get("full_name", raw_data.get("full_name", ""))
            raw_data["contact_info"] = candidate.get("contact_info", raw_data.get("contact_info", ""))
            
            print_diff_summary(resume, revised["resume"])
            resume = revised["resume"]
        else:
            logger.info("No revision notes entered. Keeping initial resume.")

    if args.dry_run:
        logger.info("DRY RUN: Outputting Final Resume JSON:")
        print(json.dumps(resume, indent=2))
    else:
        base_path = get_base_path(args.output, raw_data.get("full_name", ""), args.dir)
        save_resume_files(resume, raw_data, base_path, args.format, args)

    should_generate_cl = args.cl or prompt_yes_no(
        "Generate a tailored Cover Letter from the final resume?",
        default="n",
    )

    if should_generate_cl:
        target_role = prompt_optional("Target Role / Job Title")
        target_company = prompt_optional("Target Company")
        cover_letter = generate_cover_letter_content(
            raw_data=raw_data,
            resume=resume,
            revision_notes=revision_notes,
            target_role=target_role,
            target_company=target_company,
            api_key=api_key,
        )
        if args.dry_run:
            logger.info("DRY RUN: Outputting Cover Letter JSON:")
            print(json.dumps(cover_letter, indent=2))
        else:
            save_cover_letter_files(cover_letter, raw_data, base_path, args.format, args)

    print("Generation complete.")


if __name__ == "__main__":
    main()
