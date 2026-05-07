import re
import json
import logging
import tempfile
from pathlib import Path
import os
import hashlib
import requests
import zipfile

# Django Imports
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse, FileResponse
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.core.exceptions import SuspiciousOperation
from django.urls import reverse

# Type Imports
from dataclasses import dataclass

# Local Imports
from builder.utils import build_resume_from_payload, run_resume_workflow
from builder.jobs import submit_job
from services.filenames import sanitize_filename
from services.exceptions import ServiceUnavailableError



# Google API Imports
try:
    import google.oauth2.credentials
    import google_auth_oauthlib.flow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    
    # Allow local HTTP testing for OAuth
    if settings.DEBUG:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
except ImportError:
    pass

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive.file']
CLIENT_SECRETS_FILE = os.path.join(settings.BASE_DIR, "client_secret.json")
MAX_DOCX_UPLOAD_BYTES = 5 * 1024 * 1024
DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}
GOOGLE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
MAX_GDOC_TEXT_BYTES = 2 * 1024 * 1024
TEMP_PREFIX = "resume-gen-export-"


@dataclass(frozen=True)
class UploadedResume:
    path: str
    sha256: str

def index(request):
    """Render the Graphic-themed Resume Builder form."""
    if request.GET.get('state') and (request.GET.get('code') or request.GET.get('error')):
        return google_callback(request)

    client_id = ""
    api_key = os.environ.get('GOOGLE_CLOUD_API_KEY', '')
    
    if os.path.exists(CLIENT_SECRETS_FILE):
        with open(CLIENT_SECRETS_FILE, 'r') as f:
            try:
                data = json.load(f)
                client_id = data.get('web', {}).get('client_id', '') or data.get('installed', {}).get('client_id', '')
                # Let the environment override stale local JSON values so Picker can
                # be repaired without editing the OAuth client file.
                if not api_key:
                    api_key = data.get('web', {}).get('api_key', '') or data.get('installed', {}).get('api_key', '')
            except Exception:
                pass

    return render(request, 'builder/index.html', {
        'google_client_id': client_id,
        'google_api_key': api_key
    })

def safe_error_response(
    request,
    *,
    log_message: str,
    user_message: str = "The request could not be completed.",
    status: int = 500,
    as_json: bool = False,
    exc: Exception | None = None,
):
    if exc:
        logger.exception(log_message)
    else:
        logger.error(log_message)

    if as_json:
        return JsonResponse({"error": user_message}, status=status)

    return HttpResponse(user_message, status=status)

def validate_docx_upload(upload: UploadedFile) -> None:
    name = upload.name or ""
    if not name.lower().endswith(".docx"):
        raise SuspiciousOperation("Only .docx files are supported.")

    if upload.size and upload.size > MAX_DOCX_UPLOAD_BYTES:
        raise SuspiciousOperation("Uploaded resume is too large.")

    if upload.content_type and upload.content_type not in DOCX_MIME_TYPES:
        raise SuspiciousOperation("Invalid DOCX content type.")


def persist_docx_upload(upload: UploadedFile) -> UploadedResume:
    validate_docx_upload(upload)

    digest = hashlib.sha256()
    total = 0

    fd, tmp_path = tempfile.mkstemp(prefix="resume-upload-", suffix=".docx")
    try:
        with os.fdopen(fd, "wb") as tmp:
            for chunk in upload.chunks():
                total += len(chunk)
                if total > MAX_DOCX_UPLOAD_BYTES:
                    raise SuspiciousOperation("Uploaded resume is too large.")

                digest.update(chunk)
                tmp.write(chunk)

        if not zipfile.is_zipfile(tmp_path):
            raise SuspiciousOperation("Uploaded file is not a valid DOCX archive.")

        return UploadedResume(path=tmp_path, sha256=digest.hexdigest())

    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

def write_session_upload_file(request, file_stream, filename: str) -> None:
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix or ".docx"

    fd, tmp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(file_stream.getvalue())

        request.session["upload_file_path"] = tmp_path
        request.session["upload_file_name"] = safe_name
        request.session.modified = True

    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def cleanup_session_upload_file(request) -> None:
    path = request.session.pop("upload_file_path", None)
    request.session.pop("upload_file_name", None)
    request.session.pop("gdrive_overwrite_id", None)

    if path:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Failed to remove temporary upload file: %s", path)

def parse_resume_form(request):
    # Safely extract dynamic lists
    titles = request.POST.getlist('exp_title[]')
    companies = request.POST.getlist('exp_company[]')
    locs = request.POST.getlist('exp_location[]')
    dates = request.POST.getlist('exp_dates[]')
    bullets = request.POST.getlist('exp_bullets[]')
    
    work_experience = []
    for t, c, l, d, b in zip(titles, companies, locs, dates, bullets):
        if t or c:
            bullet_lines = [line.strip() for line in b.split('\n') if line.strip()]
            work_experience.append({
                "title": t,
                "company": c,
                "location": l,
                "dates": d,
                "bullets": bullet_lines
            })
            
    edu_insts = request.POST.getlist('edu_inst[]')
    edu_degs = request.POST.getlist('edu_deg[]')
    edu_locs = request.POST.getlist('edu_loc[]')
    edu_dates = request.POST.getlist('edu_dates[]')
    edu_details = request.POST.getlist('edu_details[]')
    
    education = []
    for ei, ed, el, edd, edit in zip(edu_insts, edu_degs, edu_locs, edu_dates, edu_details):
        if ei or ed:
            education.append({
                "institution": ei,
                "degree": ed,
                "location": el,
                "dates": edd,
                "details": edit
            })
    
    skills_raw = request.POST.get('skills', '')
    skills_list = [s.strip() for s in skills_raw.split(',') if s.strip()]

    raw_data = {
        "full_name": request.POST.get('full_name', ''),
        "contact_info": request.POST.get('contact_info', ''),
        "summary": request.POST.get('summary', ''),
        "work_experience": work_experience,
        "education": education,
        "skills": skills_list
    }
    
    # Build Options
    options = {
        "format": request.POST.get('format', 'pdf'),
        "generate_cl": request.POST.get('generate_cl') == 'on',
        "target_role": request.POST.get('target_role', ''),
        "target_company": request.POST.get('target_company', ''),
        "job_description": request.POST.get('job_description', ''),
        "revision_notes": request.POST.get('revision_notes', ''),
        "mode": request.POST.get('mode', 'scratch')
    }
    
    # Override format to docx if google docs is selected
    if options["format"] == "gdocs":
        options["format"] = "docx"
        
    source_type = request.POST.get('source_type', 'upload')
    
    file_hash_content = b"" 
    
    gdrive_file_id = request.POST.get('gdrive_file_id', '')
    gdrive_access_token = request.POST.get('gdrive_access_token', '')
    

    if options["mode"] == "revise":
        if source_type == "upload" and "resume_upload" in request.FILES:
            uploaded = persist_docx_upload(request.FILES["resume_upload"])
            options["uploaded_file"] = uploaded.path
            file_hash_content = uploaded.sha256.encode("utf-8")
        elif source_type == "gdrive" and gdrive_file_id and gdrive_access_token:
            gdoc_text, gdoc_hash = export_google_doc_text(gdrive_file_id, gdrive_access_token)
            options["gdrive_text"] = gdoc_text
            file_hash_content = gdoc_hash.encode("utf-8")
    
    return raw_data, options, file_hash_content


def compute_resume_cache_hash(
    raw_data: dict,
    options: dict,
    file_hash_content: bytes,
) -> str:
    # Only include fields that affect resume generation/revision output.
    hash_payload = {
        "raw_data": raw_data,
        "mode": options.get("mode", "scratch"),
        "revision_notes": options.get("revision_notes", ""),
    }
    return hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True).encode("utf-8") + file_hash_content
    ).hexdigest()


def generate(request):
    """Handle form submission and trigger resume generation."""
    if request.method == 'POST':
        raw_data, options, file_hash_content = parse_resume_form(request)
        original_format = request.POST.get('format', 'pdf')

        resume_hash = compute_resume_cache_hash(raw_data, options, file_hash_content)

        if request.session.get('last_resume_hash') == resume_hash:
            cached_data = cache.get(resume_hash)
            if cached_data:
                options['cached_resume'] = cached_data.get('cached_resume')
                options['cached_cover_letter'] = cached_data.get('cached_cover_letter')
                options['cached_raw_data'] = cached_data.get('cached_raw_data')

        try:
            # Delegate to our main logic
            file_stream, filename, out_resume, out_cover_letter, out_raw_data = build_resume_from_payload(raw_data, options)
            
            # Save to cache
            cache.set(resume_hash, {
                'cached_resume': out_resume,
                'cached_cover_letter': out_cover_letter,
                'cached_raw_data': out_raw_data,
            }, timeout=3600)  # 1 hour
            request.session['last_resume_hash'] = resume_hash
            
            if original_format == "gdocs":
                write_session_upload_file(request, file_stream, filename)
                request.session["gdrive_overwrite_id"] = (
                    request.POST.get("gdrive_file_id", "")
                    if request.POST.get("gdrive_overwrite") == "yes"
                    else None
                )
                return redirect("google_login")
            
            # Return the file, inline for PDFs so the browser can preview it
            is_pdf = filename.lower().endswith('.pdf')
            response = FileResponse(file_stream, as_attachment=not is_pdf, filename=filename)
            if is_pdf:
                response['Content-Disposition'] = f'inline; filename="{filename}"'
            return response
        except ServiceUnavailableError as exc:
            return safe_error_response(
                request,
                log_message="Resume generation deferred because Gemini is overloaded.",
                user_message=str(exc),
                status=503,
                exc=exc,
            )
            
        except Exception as exc:
            return safe_error_response(
                request,
                log_message="Resume generation failed.",
                user_message="Resume generation failed. Check your inputs and try again.",
                status=500,
                exc=exc,
    )
            
    return HttpResponse("Method not allowed", status=405)


def verify_ats(request):
    if request.method == 'POST':
        raw_data, options, file_hash_content = parse_resume_form(request)
        
        # We need the AI key to build and verify
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return JsonResponse({"error": "GEMINI_API_KEY is not configured."}, status=500)
            
        resume_hash = compute_resume_cache_hash(raw_data, options, file_hash_content)
        cached_data = cache.get(resume_hash)
        if cached_data:
            options['cached_resume'] = cached_data.get('cached_resume')
            options['cached_cover_letter'] = cached_data.get('cached_cover_letter')
            options['cached_raw_data'] = cached_data.get('cached_raw_data')

        try:
            def run_ats_check():
                # Force generate_cl to False so ATS standalone check doesn't redundantly wait 20s to make a cover letter
                options["generate_cl"] = False
                workflow_result = run_resume_workflow(raw_data, options)
                out_resume = workflow_result.resume
                out_raw_data = workflow_result.raw_data
                
                cache.set(resume_hash, {
                    'cached_resume': out_resume,
                    'cached_cover_letter': options.get('cached_cover_letter'),
                    'cached_raw_data': out_raw_data,
                }, timeout=3600)
                
                from services import verify_ats_compatibility
                return verify_ats_compatibility(
                    resume=out_resume,
                    target_role=options.get("target_role", ""),
                    job_description=options.get("job_description", ""),
                    api_key=api_key
                )

            job_id = submit_job(run_ats_check)
            request.session['last_resume_hash'] = resume_hash
            return JsonResponse({"job_id": job_id})
            
        except Exception as exc:
            return safe_error_response(
                request,
                log_message="ATS verification failed.",
                user_message="ATS verification failed. Check your inputs and try again.",
                status=500,
                as_json=True,
                exc=exc,
            )
    return HttpResponse("Method not allowed", status=405)

def check_job_status(request, job_id):
    job_data = cache.get(f"job:{job_id}")
    if not job_data:
        return JsonResponse({"error": "Job not found or expired."}, status=404)
    return JsonResponse(job_data)


def google_login(request):
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return HttpResponse("OAuth client_secret.json missing from project root. Please create OAuth 2.0 Credentials in Google Cloud Console, download as 'client_secret.json' and place it in the root directory.", status=500)
    
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES)
    
    # Must use build_absolute_uri to match redirect URL exactly
    flow.redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true')
    
    request.session['state'] = state
    # Save the code verifier so we can pass it in the callback request
    request.session['code_verifier'] = flow.code_verifier
    return redirect(authorization_url)


def google_callback(request):
    expected_state = request.session.get("state")
    returned_state = request.GET.get("state")
    code_verifier = request.session.get("code_verifier")

    if not expected_state or not code_verifier:
        return HttpResponse("OAuth session expired. Please reconnect Google Drive.", status=400)

    if not returned_state or returned_state != expected_state:
        raise SuspiciousOperation("OAuth state mismatch.")

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=expected_state,
    )
    flow.redirect_uri = request.build_absolute_uri(reverse("google_callback"))
    flow.code_verifier = code_verifier

    try:
        flow.fetch_token(authorization_response=request.build_absolute_uri())
    except Exception as exc:
        return safe_error_response(
            request,
            log_message="Google OAuth token exchange failed.",
            user_message="Google authorization failed. Please try again.",
            status=400,
            exc=exc,
        )

    request.session.pop("state", None)
    request.session.pop("code_verifier", None)

    credentials = flow.credentials
    
    file_path = request.session.get('upload_file_path')
    file_name = request.session.get('upload_file_name')
    
    if not file_path or not os.path.exists(file_path):
        return HttpResponse("Generated file missing from session.", status=404)
        
    try:
        drive_service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
        
        is_zip = file_path.endswith('.zip')
        source_mimetype = 'application/zip' if is_zip else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        
        media = MediaFileUpload(file_path,
                                mimetype=source_mimetype,
                                resumable=True)
                                
        overwrite_id = request.session.get('gdrive_overwrite_id')
        
        if overwrite_id and not is_zip:
            # Overwrite existing Google Doc
            # When updating, we don't set the name/mimeType metadata again as it inherits the original file
            uploaded_file = drive_service.files().update(
                fileId=overwrite_id,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
        else:
            # Create NEW Google Doc
            file_metadata = {
                'name': file_name.replace('.docx', '')
            }
            if not is_zip:
                file_metadata['mimeType'] = 'application/vnd.google-apps.document'
                
            uploaded_file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
        
        # Clean up local file
        os.remove(file_path)
        
        # Redirect to the Google Doc!
        return redirect(uploaded_file.get("webViewLink"))
    except Exception as exc:
        return safe_error_response(
            request,
            log_message="Google Drive upload failed.",
            user_message="Google Drive upload failed.",
            status=500,
            exc=exc,
        )
    finally:
        cleanup_session_upload_file(request)

def export_google_doc_text(file_id: str, access_token: str) -> tuple[str, str]:
    if not GOOGLE_FILE_ID_RE.fullmatch(file_id or ""):
        raise SuspiciousOperation("Invalid Google file ID.")

    if not access_token or len(access_token) > 4096:
        raise SuspiciousOperation("Invalid Google access token.")

    export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"mimeType": "text/plain"}

    response = requests.get(
        export_url,
        headers=headers,
        params=params,
        timeout=(3.05, 20),
        stream=True,
    )

    if response.status_code != 200:
        raise SuspiciousOperation("Could not export selected Google Doc.")

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0

    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue

        total += len(chunk)
        if total > MAX_GDOC_TEXT_BYTES:
            raise SuspiciousOperation("Google Doc export is too large.")

        digest.update(chunk)
        chunks.append(chunk)

    return b"".join(chunks).decode("utf-8", errors="replace"), digest.hexdigest()