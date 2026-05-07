import os
from pathlib import Path
from typing import Any
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
except ImportError as exc:
    GOOGLE_IMPORT_ERROR = exc
else:
    GOOGLE_IMPORT_ERROR = None


def require_google_dependencies() -> None:
    if GOOGLE_IMPORT_ERROR is not None:
        raise ConfigurationError(
            "Google Drive dependencies are not installed. "
            "Install google-api-python-client, google-auth, and google-auth-oauthlib."
        ) from GOOGLE_IMPORT_ERROR

logger = logging.getLogger(__name__)
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def _token_path() -> Path:
    root = Path(os.getenv("RESUME_GEN_CONFIG_DIR", "~/.config/resume-gen")).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / "google-token.json"


def _client_secret_path() -> Path:
    path = Path(os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json")).expanduser()
    if not path.exists():
        raise ConfigurationError("Google OAuth client secret file was not found.")
    return path


def get_google_credentials() -> Any:
    require_google_dependencies()
    token_path = _token_path()
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret_path()), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_path, 0o600)

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
