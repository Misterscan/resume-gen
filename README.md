# Resume & Cover Letter Generator with Gemini AI

A powerful Python command-line application that generates and revises ATS-optimized resumes and tailored Cover Letters using Google's Gemini models (`gemini-3.1-pro-preview`). 

## Features

- **Resume Generation**: Enter your professional facts and achievements, and let Gemini craft an ATS-friendly resume using the Google XYZ formula: "Accomplished [X] as measured by [Y], by doing [Z]."
- **Cover Letter Generation**: Optionally generate a compelling, role-specific cover letter tailored to a target company and position that highlights your relevance and tells a coherent story connecting to your resume.
- **DOCX Revision Support**: Feed an existing `.docx` resume into the CLI along with revision notes to have Gemini rewrite and dramatically improve its impact.
- **Multiple Output Formats**: Export automatically to `.docx` and `.pdf` formats.
- **Interactive Prompts**: Simple interactive flows for data collection, corrections, and adjustments.

## Prerequisites

- Python 3.9+
- A Google Gemini API Key

## Installation

1. Clone this repository or download the source files.

```bash
git clone https://github.com/Misterscan/resume-gen.git
cd resume-gen
```

2. Create a virtual environment

```bash
python3 -m venv .venv
.venv\Scripts\activate
```
3. Install the required Python packages:

```bash
pip install -r requirements.txt
```

4. Set your Google Gemini API key as an environment variable:

```bash
export GEMINI_API_KEY="your-api-key-here"
```

## Usage

You can run the script interactively or use command-line arguments to bypass certain prompts.

### Basic Interactive Use

Run the program and follow the prompts to build a resume from scratch:

```bash
python main.py
```

### Full Command Line Options

```text
usage: main.py [-h] [--format {pdf,docx,both}] [--output OUTPUT]
               [--revise] [--cl] [--input INPUT] [--notes NOTES]

Generate an ATS-optimized resume and optional cover letter with Gemini.

options:
  -h, --help            show this help message and exit
  --format {pdf,docx,both}
                        Output format. (default: both)
  --output OUTPUT       Target base filename without extension. (default: [Your Name] Resume // [Your Name] Cover Letter)
  --revise              Prompt for missing data or corrections after initial resume generation.
  --cl        Automatically generate a tailored cover letter after resume creation or revision.
  --input INPUT         Existing .docx resume to revise instead of starting from scratch.
  --notes NOTES         Revision instructions for --input mode.
```

### Examples

**1. Generate a Base Resume & Cover Letter without further interactive prompting:**
```bash
python main.py --cl
```

**2. Revise an Existing Resume (.docx):**
If you already have a resume, you can let Gemini upgrade it:
```bash
python main.py --input old_resume.docx --notes "Focus on my Python & AI engineering experience, shorten the early career bullets." --output 'John Doe Updated Resume May 2026'
```
Alternatively, you can revise a newly generated resume:
```bash
python main.py --revise --notes "Add 1-2 enhancements to my skills and work experience sections, make me sound more experienced."
```

## Output

Generated files are saved by default into a `resumes` directory alongside your specified output path:
- `resumes/John Doe Resume.pdf`
- `resumes/John Doe Resume.docx`
- `resumes/John Doe Cover Letter.pdf` (If Cover Letter generation was requested)
- `resumes/John Doe Cover Letter.docx`

## Architecture

- Uses `google-genai` to interface with the Gemini 3.1 Pro Preview API.
- Generates precise JSON schemas natively utilizing Gemini's structured JSON response capabilities.
- Programmatically formats outputs locally via `python-docx` for MS Word compatibility and custom `fpdf2` logic for clean PDFs.