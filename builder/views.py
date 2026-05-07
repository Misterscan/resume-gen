import json
import logging
import tempfile
import os
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse, FileResponse
from django.conf import settings
from .utils import build_resume_from_payload
from django.urls import reverse

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

import hashlib

def generate(request):
    """Handle form submission and trigger resume generation."""
    if request.method == 'POST':
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
            "revision_notes": request.POST.get('revision_notes', ''),
            "mode": request.POST.get('mode', 'scratch')
        }
        
        # Override format to docx if google docs is selected
        original_format = options["format"]
        if options["format"] == "gdocs":
            options["format"] = "docx"
            
        source_type = request.POST.get('source_type', 'upload')
        
        uploaded_file_path = None
        file_hash_content = b""
        
        gdrive_file_id = request.POST.get('gdrive_file_id', '')
        gdrive_access_token = request.POST.get('gdrive_access_token', '')
        gdrive_overwrite = request.POST.get('gdrive_overwrite') == 'yes'

        if options['mode'] == 'revise':
            if source_type == 'upload' and 'resume_upload' in request.FILES:
                upload = request.FILES['resume_upload']
                if upload.name.endswith('.docx'):
                    fd, tmp_file = tempfile.mkstemp(suffix='.docx')
                    with os.fdopen(fd, 'wb') as f:
                        for chunk in upload.chunks():
                            f.write(chunk)
                            file_hash_content += chunk
                    options["uploaded_file"] = tmp_file
            elif source_type == 'gdrive' and gdrive_file_id and gdrive_access_token:
                try:
                    import requests
                    # Export the GDoc as plain text for the AI
                    export_url = f"https://www.googleapis.com/drive/v3/files/{gdrive_file_id}/export?mimeType=text/plain"
                    headers = {"Authorization": f"Bearer {gdrive_access_token}"}
                    response = requests.get(export_url, headers=headers)
                    if response.status_code == 200:
                        options["gdrive_text"] = response.text
                        file_hash_content = response.text.encode('utf-8')
                    else:
                        logger.error(f"Failed to export GDoc: {response.status_code} {response.text}")
                except Exception as e:
                    logger.error(f"Error fetching GDoc: {str(e)}")
        
        # Calculate a state hash to cache the LLM output
        hash_payload = {
            "raw_data": raw_data,
            "mode": options["mode"],
            "generate_cl": options["generate_cl"],
            "target_role": options["target_role"],
            "target_company": options["target_company"],
            "revision_notes": options["revision_notes"]
        }
        payload_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True).encode('utf-8') + file_hash_content).hexdigest()
        
        if request.session.get('last_generation_hash') == payload_hash:
            options['cached_resume'] = request.session.get('cached_resume')
            options['cached_cover_letter'] = request.session.get('cached_cover_letter')
            options['cached_raw_data'] = request.session.get('cached_raw_data')

        try:
            # Delegate to our main logic
            file_path, filename, out_resume, out_cover_letter, out_raw_data = build_resume_from_payload(raw_data, options)
            
            # Save to cache
            request.session['last_generation_hash'] = payload_hash
            request.session['cached_resume'] = out_resume
            request.session['cached_cover_letter'] = out_cover_letter
            request.session['cached_raw_data'] = out_raw_data
            
            if original_format == "gdocs":
                request.session['upload_file_path'] = file_path
                request.session['upload_file_name'] = filename
                
                # Check if we should overwrite
                if gdrive_overwrite and gdrive_file_id:
                    request.session['gdrive_overwrite_id'] = gdrive_file_id
                else:
                    request.session['gdrive_overwrite_id'] = None
                    
                return redirect('google_login')
            
            # Return the file, inline for PDFs so the browser can preview it
            is_pdf = filename.lower().endswith('.pdf')
            response = FileResponse(open(file_path, 'rb'), as_attachment=not is_pdf, filename=filename)
            if is_pdf:
                response['Content-Disposition'] = f'inline; filename="{filename}"'
            return response
            
        except Exception as e:
            logger.error(f"Failed to generate resume: {str(e)}")
            return HttpResponse(f"Error generating resume: {str(e)}", status=500)
            
    return HttpResponse("Method not allowed", status=405)


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
    state = request.session.get('state')
    code_verifier = request.session.get('code_verifier')
    
    if not state or not code_verifier:
        return HttpResponse("Session state missing. Please try again.", status=400)
        
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    
    # Restore the code verifier
    flow.code_verifier = code_verifier
    
    authorization_response = request.build_absolute_uri()
    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Exception as e:
        return HttpResponse(f"Failed to fetch token: {str(e)}", status=400)
        
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
        return redirect(uploaded_file.get('webViewLink'))
        
    except Exception as e:
         import traceback
         error_trace = traceback.format_exc()
         logger.error(f"Error uploading to Google Drive:\n{error_trace}")
         return HttpResponse(f"Error uploading to Google Drive: {str(e)}<br><pre>{error_trace}</pre>", status=500)
