import os
import io
import logging
from typing import Any, Optional
from .exceptions import ConfigurationError, IntegrationError

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
except ImportError:
    pass

logger = logging.getLogger(__name__)
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def get_google_credentials() -> Any:
    creds = None
    token_path = 'token.json'
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Inside get_google_credentials
            # Allow loading from ENV for containerized deployments
            if not os.path.exists('client_secret.json') and 'GOOGLE_CLIENT_SECRET_JSON' in os.environ:
            # Use json.loads(os.environ['GOOGLE_CLIENT_SECRET_JSON']) 
            # and use Credentials.from_authorized_user_info
                raise ConfigurationError("client_secret.json not found for Google Docs integration.")
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    return creds

def get_gdoc_text(file_id: str, creds: Any) -> str:
    try:
        drive_service = build('drive', 'v3', credentials=creds)
        request = drive_service.files().export_media(fileId=file_id, mimeType='text/plain')
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue().decode('utf-8')
    except Exception as exc:
        logger.error(f"Failed to read Google Doc: {exc}")
        raise IntegrationError(f"Failed to read Google Doc: {exc}")

def upload_to_gdoc(file_path: str, file_name: str, creds: Any, overwrite_id: Optional[str] = None) -> str:
    try:
        drive_service = build('drive', 'v3', credentials=creds)
        media = MediaFileUpload(str(file_path), mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', resumable=True)
        if overwrite_id:
            uploaded_file = drive_service.files().update(
                fileId=overwrite_id,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
        else:
            file_metadata = {
                'name': file_name.replace('.docx', ''),
                'mimeType': 'application/vnd.google-apps.document'
            }
            uploaded_file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
        return uploaded_file.get('webViewLink', '')
    except Exception as exc:
        logger.error(f"Failed to upload to Google Docs: {exc}")
        raise IntegrationError(f"Failed to upload to Google Docs: {exc}")
