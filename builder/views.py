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
from django.core.files.base import ContentFile
from django.core.exceptions import SuspiciousOperation
from django.urls import reverse
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.decorators import login_required
from .models import Profile, Resume, CoverLetter
from django.contrib.auth.models import User
from django.contrib import messages

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
    # Check if user indicated no work experience
    no_work_experience_value = (request.POST.get('no_work_experience') or '').strip().lower()
    no_work_experience = no_work_experience_value in {'on', 'yes', 'true', '1'}
    alternative_experience_text = request.POST.get('alternative_experience_text', '').strip()
    
    # Safely extract dynamic lists
    titles = request.POST.getlist('exp_title[]')
    companies = request.POST.getlist('exp_company[]')
    locs = request.POST.getlist('exp_location[]')
    dates = request.POST.getlist('exp_dates[]')
    bullets = request.POST.getlist('exp_bullets[]')
    
    work_experience = []
    # Only process work experience if user didn't indicate they have none
    if not no_work_experience:
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
    edu_credential_types = request.POST.getlist('edu_credential_type[]')
    edu_degs = request.POST.getlist('edu_deg[]')
    edu_locs = request.POST.getlist('edu_loc[]')
    edu_dates = request.POST.getlist('edu_dates[]')
    edu_details = request.POST.getlist('edu_details[]')

    if not edu_credential_types:
        edu_credential_types = [""] * len(edu_degs)
    
    education = []
    for ei, et, ed, el, edd, edit in zip(edu_insts, edu_credential_types, edu_degs, edu_locs, edu_dates, edu_details):
        et = (et or "").strip()
        ed = (ed or "").strip()
        extra_details = (edit or "").strip()

        if et in {"GED", "GED (High School Equivalency)", "High School", "High School Diploma"}:
            normalized_degree = et
            if ed:
                extra_details = f"Program details: {ed}" if not extra_details else f"Program details: {ed}; {extra_details}"
        elif et and ed:
            normalized_degree = ed
            extra_details = f"Education type: {et}" if not extra_details else f"Education type: {et}; {extra_details}"
        else:
            normalized_degree = et or ed

        if ei or et or ed:
            education.append({
                "institution": ei,
                "degree": normalized_degree,
                "location": el,
                "dates": edd,
                "details": extra_details
            })

    project_names = request.POST.getlist('project_name[]')
    project_orgs = request.POST.getlist('project_org[]')
    project_locs = request.POST.getlist('project_loc[]')
    project_dates = request.POST.getlist('project_dates[]')
    project_bullets = request.POST.getlist('project_bullets[]')

    projects = []
    for name, org, loc, dates_value, bullets_value in zip(project_names, project_orgs, project_locs, project_dates, project_bullets):
        if name:
            projects.append({
                "name": name,
                "organization": org,
                "location": loc,
                "dates": dates_value,
                "bullets": [line.strip() for line in bullets_value.split('\n') if line.strip()],
            })

    volunteer_roles = request.POST.getlist('volunteer_role[]')
    volunteer_orgs = request.POST.getlist('volunteer_org[]')
    volunteer_locs = request.POST.getlist('volunteer_loc[]')
    volunteer_dates = request.POST.getlist('volunteer_dates[]')
    volunteer_bullets = request.POST.getlist('volunteer_bullets[]')

    volunteer_experience = []
    for role, org, loc, dates_value, bullets_value in zip(volunteer_roles, volunteer_orgs, volunteer_locs, volunteer_dates, volunteer_bullets):
        if role or org:
            volunteer_experience.append({
                "role": role,
                "organization": org,
                "location": loc,
                "dates": dates_value,
                "bullets": [line.strip() for line in bullets_value.split('\n') if line.strip()],
            })

    cert_names = request.POST.getlist('cert_name[]')
    cert_issuers = request.POST.getlist('cert_issuer[]')
    cert_dates = request.POST.getlist('cert_dates[]')
    cert_details = request.POST.getlist('cert_details[]')

    certifications = []
    for name, issuer, dates_value, details in zip(cert_names, cert_issuers, cert_dates, cert_details):
        if name:
            certifications.append({
                "name": name,
                "issuer": issuer,
                "dates": dates_value,
                "details": details,
            })
    
    skills_raw = request.POST.get('skills', '')
    skills_list = [s.strip() for s in skills_raw.split(',') if s.strip()]

    raw_data = {
        "full_name": request.POST.get('full_name', ''),
        "contact_info": request.POST.get('contact_info', ''),
        "summary": request.POST.get('summary', ''),
        "no_work_experience": no_work_experience,
        "alternative_experience_text": alternative_experience_text,
        "work_experience": work_experience,
        "projects": projects,
        "volunteer_experience": volunteer_experience,
        "certifications": certifications,
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

        if options.get("mode") == "ats_fix":
            source_hash = (request.POST.get("ats_source_hash") or "").strip() or request.session.get("last_resume_hash")
            source_cached_data = cache.get(source_hash) if source_hash else None
            if source_cached_data:
                options['cached_resume'] = source_cached_data.get('cached_resume')
                options['cached_cover_letter'] = source_cached_data.get('cached_cover_letter')
                options['cached_raw_data'] = source_cached_data.get('cached_raw_data')
            else:
                return safe_error_response(
                    request,
                    log_message="ATS fix requested without cached ATS baseline resume.",
                    user_message="Run ATS check first, then click Fix Resume Using ATS Suggestions.",
                    status=400,
                )

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

            # Save to database if user is logged in
            if request.user.is_authenticated:
                resume_file = ContentFile(file_stream.getvalue())
                Resume.objects.create(
                    user=request.user,
                    title=filename,
                    file=resume_file,
                    hash=resume_hash,
                    version_type=options.get("mode", "generated")
                )
                # If cover letter was also generated, save it
                if out_cover_letter:
                    CoverLetter.objects.create(
                        user=request.user,
                        title=f"{filename.replace('.pdf', '').replace('.docx', '')} Cover Letter",
                        content=json.dumps(out_cover_letter)
                    )
                # Reset stream pointer for response
                file_stream.seek(0)
            
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
            return JsonResponse({"job_id": job_id, "resume_hash": resume_hash})
            
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


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


# User Registration View
def register(request):
    if request.method == 'POST':
        # Simple rate limit for registration
        ip = get_client_ip(request)
        reg_key = f"reg_limit_{ip}"
        count = cache.get(reg_key, 0)
        if count >= 5:
            return HttpResponse("Too many registration attempts. Please try again later.", status=429)
        cache.set(reg_key, count + 1, timeout=3600)

        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.create(user=user)  # Create a profile for the user
            login(request, user)
            return redirect('profile')
    else:
        form = UserCreationForm()
    return render(request, 'builder/register.html', {'form': form})

# User Login View
def user_login(request):
    if request.method == 'POST':
        # Simple rate limit for login
        ip = get_client_ip(request)
        login_key = f"login_limit_{ip}"
        count = cache.get(login_key, 0)
        if count >= 10:
            return HttpResponse("Too many login attempts. Please try again later.", status=429)

        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            cache.delete(login_key) # Reset on success
            return redirect('profile')
        else:
            cache.set(login_key, count + 1, timeout=300)
            return render(request, 'builder/login.html', {'error': 'Invalid username or password.', 'form': form})
    else:
        form = AuthenticationForm()
    return render(request, 'builder/login.html', {'form': form})

# User Logout View
def user_logout(request):
    logout(request)
    return redirect('login')

# User Profile View
@login_required
def profile(request):
    resumes = Resume.objects.filter(user=request.user).order_by('-created_at')
    cover_letters = CoverLetter.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'builder/profile.html', {
        'resumes': resumes,
        'cover_letters': cover_letters,
    })