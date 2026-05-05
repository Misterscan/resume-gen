from google import genai
from google.genai import types

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from docx import Document as create_document
from docx.document import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt
from docx.styles.style import ParagraphStyle
from fpdf import FPDF


MODEL_NAME = "gemini-3.1-pro-preview"

RESUME_SYSTEM_PROMPT = """
You are a Professional Resume Writer specializing in ATS-optimized resumes.

Transform raw professional data into a polished resume using:
- Clear, achievement-oriented language.
- The Google XYZ formula:
  Accomplished [X] as measured by [Y], by doing [Z].
- Strong action verbs.
- Quantified impact wherever numbers are provided or can be responsibly inferred.
- Conservative wording when metrics are absent.

Strict output rules:
- Return valid JSON only.
- No markdown.
- No commentary.
- No tables.
- No images.
- No multi-column layout instructions.
- Use these exact top-level keys:
  professional_summary, work_experience, education, skills.
- work_experience must be a list of objects with:
  title, company, location, dates, bullets.
- education must be a list of objects with:
  institution, degree, location, dates, details.
- skills must be a list of concise skill strings.
- Keep bullets ATS-friendly, specific, and measurable.
- Use standard resume headings:
  Professional Summary, Work Experience, Education, Skills.
"""

REVISION_SYSTEM_PROMPT = """
You are a senior Professional Resume Writer and ATS optimization specialist.

Revise a resume using either structured resume data or text extracted from an
existing DOCX file. Use the user's revision notes as the controlling source for
new or corrected facts. Preserve truthful information. Do not invent employers,
degrees, dates, credentials, awards, publications, or metrics.

Improve specificity, impact, clarity, and ATS compatibility. Use the Google XYZ
formula where possible:
Accomplished [X] as measured by [Y], by doing [Z].

Strict output rules:
- Return valid JSON only.
- No markdown.
- No commentary.
- No tables.
- No images.
- No multi-column layout instructions.
- Use these exact top-level keys: candidate, resume.
- candidate must be an object with: full_name, contact_info.
- resume must be an object with:
  professional_summary, work_experience, education, skills.
- resume.work_experience must be a list of objects with:
  title, company, location, dates, bullets.
- resume.education must be a list of objects with:
  institution, degree, location, dates, details.
- resume.skills must be a list of concise skill strings.
- Use standard resume headings only:
  Professional Summary, Work Experience, Education, Skills.
"""

COVER_LETTER_SYSTEM_PROMPT = """
You are an Expert Career Coach and Professional Resume Writer.

Create a compelling, tailored cover letter (roughly 250–400 words) from the user's resume data,
revision notes, and the target role/company.

The cover letter must:
- Connect the applicant's resume to the specific job requirements.
- Tell a concise story highlighting why they are the ideal fit.
- Use an introductory paragraph stating the position and interest.
- Use 1-2 body paragraphs providing specific examples of aligned qualifications, accomplishments, and skills.
- Connect to the company's culture or values based on available context.
- End with a strong closing and call to action.

Strict output rules:
- Return valid JSON only.
- No markdown formatting.
- No commentary.
- Use these exact top-level keys:
  recipient_info, greeting, introduction, body_paragraphs, company_connection, closing, sign_off
- recipient_info: string (e.g., "Hiring Manager\n[Company Name]").
- greeting: string (e.g., "Dear Hiring Manager,").
- introduction: string.
- body_paragraphs: list of specific accomplishment/skill paragraphs (strings).
- company_connection: string (why this company).
- closing: string (call to action).
- sign_off: string (e.g., "Sincerely,").
"""



def require_api_key() -> str:
    try:
        api_key = os.environ["GEMINI_API_KEY"]
    except KeyError:
        print(
            "ERROR: GEMINI_API_KEY environment variable is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not api_key.strip():
        print("ERROR: GEMINI_API_KEY is empty.", file=sys.stderr)
        sys.exit(1)

    return api_key


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


def extract_docx_text(input_path: Path) -> str:
    try:
        document = create_document(str(input_path))
        paragraphs = [
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]
        text = "\n".join(paragraphs).strip()
        if not text:
            raise ValueError("The DOCX file did not contain readable text.")
        return text
    except Exception as exc:
        print(f"ERROR: Failed to read DOCX file: {exc}", file=sys.stderr)
        sys.exit(1)


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
            raise ValueError("Gemini returned an empty response.")

        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            raise ValueError("Gemini returned JSON, but not a JSON object.")

        return parsed

    except Exception as exc:
        print(f"ERROR: Gemini generation failed: {exc}", file=sys.stderr)
        sys.exit(1)


def generate_resume_content(
    raw_data: Dict[str, Any],
    api_key: str,
) -> Dict[str, Any]:
    return call_gemini_json(
        api_key=api_key,
        system_prompt=RESUME_SYSTEM_PROMPT,
        payload={"raw_data": raw_data},
        temperature=0.3,
    )



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

    result = call_gemini_json(
        api_key=api_key,
        system_prompt=REVISION_SYSTEM_PROMPT,
        payload=payload,
        temperature=0.2,
    )

    candidate = result.get("candidate")
    resume = result.get("resume")

    if not isinstance(candidate, dict) or not isinstance(resume, dict):
        print(
            "ERROR: Gemini did not return the expected revision schema.",
            file=sys.stderr,
        )
        sys.exit(1)

    return result


def generate_cover_letter_content(
    raw_data: Dict[str, Any],
    resume: Dict[str, Any],
    revision_notes: Optional[str],
    target_role: str,
    target_company: str,
    api_key: str,
) -> Dict[str, Any]:
    return call_gemini_json(
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


def add_heading(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)


def configure_docx(document: Document) -> None:
    normal_style = document.styles["Normal"]

    if normal_style.type == WD_STYLE_TYPE.PARAGRAPH:
        paragraph_style = normal_style
        assert isinstance(paragraph_style, ParagraphStyle)
        paragraph_style.font.name = "Arial"
        paragraph_style.font.size = Pt(10)


def save_as_docx(
    resume: Dict[str, Any],
    raw_data: Dict[str, Any],
    output_path: Path,
) -> None:
    try:
        document = create_document()
        configure_docx(document)

        name = document.add_paragraph()
        name_run = name.add_run(raw_data["full_name"])
        name_run.bold = True
        name_run.font.size = Pt(16)

        document.add_paragraph(raw_data["contact_info"])

        add_heading(document, "Professional Summary")
        document.add_paragraph(resume.get("professional_summary", ""))

        add_heading(document, "Work Experience")
        for item in resume.get("work_experience", []):
            role = document.add_paragraph()
            role_run = role.add_run(
                f"{item.get('title', '')} | "
                f"{item.get('company', '')} | "
                f"{item.get('location', '')} | "
                f"{item.get('dates', '')}"
            )
            role_run.bold = True

            for bullet in item.get("bullets", []):
                document.add_paragraph(bullet, style="List Bullet")

        add_heading(document, "Education")
        for item in resume.get("education", []):
            edu = document.add_paragraph()
            edu_run = edu.add_run(
                f"{item.get('degree', '')} | "
                f"{item.get('institution', '')} | "
                f"{item.get('location', '')} | "
                f"{item.get('dates', '')}"
            )
            edu_run.bold = True

            details = item.get("details", "")
            if details:
                document.add_paragraph(details)

        add_heading(document, "Skills")
        document.add_paragraph(", ".join(resume.get("skills", [])))

        document.save(str(output_path))

    except Exception as exc:
        print(f"ERROR: Failed to write DOCX: {exc}", file=sys.stderr)
        sys.exit(1)


def save_cover_letter_as_docx(
    cover_letter: Dict[str, Any],
    raw_data: Dict[str, Any],
    output_path: Path,
) -> None:
    try:
        document = create_document()
        configure_docx(document)

        name = document.add_paragraph()
        name_run = name.add_run(raw_data["full_name"])
        name_run.bold = True
        name_run.font.size = Pt(16)

        document.add_paragraph(raw_data["contact_info"])
        document.add_paragraph("")  # spacing

        # Date
        import datetime
        document.add_paragraph(datetime.date.today().strftime("%B %d, %Y"))
        document.add_paragraph("")  # spacing

        if cover_letter.get("recipient_info"):
            document.add_paragraph(cover_letter["recipient_info"])
            document.add_paragraph("")

        if cover_letter.get("greeting"):
            document.add_paragraph(cover_letter["greeting"])
            document.add_paragraph("")

        if cover_letter.get("introduction"):
            document.add_paragraph(cover_letter["introduction"])
            document.add_paragraph("")

        for paragraph in cover_letter.get("body_paragraphs", []):
            document.add_paragraph(paragraph)
            document.add_paragraph("")

        if cover_letter.get("company_connection"):
            document.add_paragraph(cover_letter["company_connection"])
            document.add_paragraph("")

        if cover_letter.get("closing"):
            document.add_paragraph(cover_letter["closing"])
            document.add_paragraph("")

        if cover_letter.get("sign_off"):
            document.add_paragraph(cover_letter["sign_off"])

        document.add_paragraph(raw_data.get("full_name", ""))

        document.save(str(output_path))

    except Exception as exc:
        print(f"ERROR: Failed to write Cover Letter DOCX: {exc}", file=sys.stderr)
        sys.exit(1)


class ResumePDF(FPDF):
    def usable_width(self) -> float:
        return self.w - self.l_margin - self.r_margin

    @staticmethod
    def clean_text(text: Any) -> str:
        return (
            str(text)
            .replace("\u2013", "-")
            .replace("\u2014", "-")
            .replace("\u2018", "'")
            .replace("\u2019", "'")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .strip()
        )

    def section_heading(self, text: str) -> None:
        self.ln(4)
        self.set_font("Helvetica", "B", 11)
        self.set_x(self.l_margin)
        self.cell(
            self.usable_width(),
            7,
            self.clean_text(text).upper(),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.set_font("Helvetica", "", 10)

    def paragraph(self, text: Any) -> None:
        self.set_x(self.l_margin)
        self.multi_cell(
            self.usable_width(),
            5,
            self.clean_text(text),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.ln(1)

    def bullet(self, text: Any) -> None:
        self.set_x(self.l_margin)
        self.multi_cell(
            self.usable_width(),
            5,
            f"- {self.clean_text(text)}",
            new_x="LMARGIN",
            new_y="NEXT",
        )

    def bold_line(self, text: Any) -> None:
        self.set_font("Helvetica", "B", 10)
        self.set_x(self.l_margin)
        self.multi_cell(
            self.usable_width(),
            5,
            self.clean_text(text),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.set_font("Helvetica", "", 10)


def create_pdf() -> ResumePDF:
    pdf = ResumePDF()
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    return pdf


def write_pdf_header(pdf: ResumePDF, raw_data: Dict[str, Any]) -> None:
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_x(pdf.l_margin)
    pdf.cell(
        pdf.usable_width(),
        8,
        pdf.clean_text(raw_data["full_name"]),
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.set_font("Helvetica", "", 10)
    pdf.paragraph(raw_data.get("contact_info", ""))


def save_as_pdf(
    resume: Dict[str, Any],
    raw_data: Dict[str, Any],
    output_path: Path,
) -> None:
    try:
        pdf = create_pdf()
        write_pdf_header(pdf, raw_data)

        pdf.section_heading("Professional Summary")
        pdf.paragraph(resume.get("professional_summary", ""))

        pdf.section_heading("Work Experience")
        for item in resume.get("work_experience", []):
            pdf.bold_line(
                f"{item.get('title', '')} | "
                f"{item.get('company', '')} | "
                f"{item.get('location', '')} | "
                f"{item.get('dates', '')}"
            )

            for bullet in item.get("bullets", []):
                pdf.bullet(bullet)

            pdf.ln(2)

        pdf.section_heading("Education")
        for item in resume.get("education", []):
            pdf.bold_line(
                f"{item.get('degree', '')} | "
                f"{item.get('institution', '')} | "
                f"{item.get('location', '')} | "
                f"{item.get('dates', '')}"
            )

            details = item.get("details", "")
            if details:
                pdf.paragraph(details)

        pdf.section_heading("Skills")
        pdf.paragraph(", ".join(resume.get("skills", [])))

        pdf.output(str(output_path))

    except Exception as exc:
        print(f"ERROR: Failed to write PDF: {exc}", file=sys.stderr)
        sys.exit(1)


def save_cover_letter_as_pdf(
    cover_letter: Dict[str, Any],
    raw_data: Dict[str, Any],
    output_path: Path,
) -> None:
    try:
        pdf = create_pdf()
        write_pdf_header(pdf, raw_data)

        pdf.ln(5)

        import datetime
        pdf.paragraph(datetime.date.today().strftime("%B %d, %Y"))
        pdf.ln(2)

        if cover_letter.get("recipient_info"):
            for line in cover_letter["recipient_info"].split("\n"):
                pdf.paragraph(line)
            pdf.ln(2)

        if cover_letter.get("greeting"):
            pdf.paragraph(cover_letter["greeting"])
            pdf.ln(2)

        if cover_letter.get("introduction"):
            pdf.paragraph(cover_letter["introduction"])
            pdf.ln(2)

        for paragraph in cover_letter.get("body_paragraphs", []):
            pdf.paragraph(paragraph)
            pdf.ln(2)

        if cover_letter.get("company_connection"):
            pdf.paragraph(cover_letter["company_connection"])
            pdf.ln(2)

        if cover_letter.get("closing"):
            pdf.paragraph(cover_letter["closing"])
            pdf.ln(4)

        if cover_letter.get("sign_off"):
            pdf.paragraph(cover_letter["sign_off"])

        pdf.paragraph(raw_data.get("full_name", ""))

        pdf.output(str(output_path))

    except Exception as exc:
        print(f"ERROR: Failed to write Cover Letter PDF: {exc}", file=sys.stderr)
        sys.exit(1)

def get_base_path(output_arg: Optional[str], full_name: str) -> Path:
    if output_arg:
        target = output_arg
    elif full_name:
        target = f"{full_name} Resume"
    else:
        target = "My Resume"
    
    target = target.replace("/", "_").replace("\\", "_")
    output_path = Path(target).expanduser().resolve()
    output_folder = output_path.parent / "resumes"
    output_folder.mkdir(parents=True, exist_ok=True)
    return output_folder / output_path.stem

def save_resume_files(
    resume: Dict[str, Any],
    raw_data: Dict[str, Any],
    base_path: Path,
    output_format: str,
) -> None:
    if output_format in {"docx", "both"}:
        save_as_docx(resume, raw_data, base_path.with_suffix(".docx"))

    if output_format in {"pdf", "both"}:
        save_as_pdf(resume, raw_data, base_path.with_suffix(".pdf"))


def save_cover_letter_files(
    cover_letter: Dict[str, Any],
    raw_data: Dict[str, Any],
    base_path: Path,
    output_format: str,
) -> None:
    cl_base_path = base_path.with_name(f"{base_path.name} Cover Letter")

    if output_format in {"docx", "both"}:
        save_cover_letter_as_docx(cover_letter, raw_data, cl_base_path.with_suffix(".docx"))

    if output_format in {"pdf", "both"}:
        save_cover_letter_as_pdf(cover_letter, raw_data, cl_base_path.with_suffix(".pdf"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an ATS-optimized resume and optional cover letter with Gemini."
    )
    parser.add_argument(
        "--format",
        choices=["pdf", "docx", "both"],
        default="both",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Target base filename without extension. Defaults to '<Full Name> Resume'.",
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
        "--notes",
        help="Revision instructions for --input mode.",
    )
    return parser.parse_args()



def main() -> None:
    args = parse_args()
    api_key = require_api_key()

    if args.input:
        input_path = args.input.expanduser().resolve()
        if input_path.suffix.lower() != ".docx":
            print("ERROR: --input currently supports .docx files only.", file=sys.stderr)
            sys.exit(1)
        if not input_path.exists():
            print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)

        resume_text = extract_docx_text(input_path)
        revision_notes = args.notes or collect_revision_notes()
        if not revision_notes:
            print("ERROR: Revision notes are required in --input mode.", file=sys.stderr)
            sys.exit(1)

        revised = revise_resume_content(
            api_key=api_key,
            revision_notes=revision_notes,
            resume_text=resume_text,
        )
        raw_data = revised["candidate"]
        resume = revised["resume"]
        raw_data["source_resume_text"] = resume_text

        base_path = get_base_path(args.output, raw_data.get("full_name", ""))
        save_resume_files(resume, raw_data, base_path, args.format)

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
            save_cover_letter_files(cover_letter, raw_data, base_path, args.format)

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
            resume = revised["resume"]
        else:
            print("No revision notes entered. Keeping initial resume.")

    base_path = get_base_path(args.output, raw_data.get("full_name", ""))
    save_resume_files(resume, raw_data, base_path, args.format)

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
        save_cover_letter_files(cover_letter, raw_data, base_path, args.format)

    print("Generation complete.")


if __name__ == "__main__":
    main()
