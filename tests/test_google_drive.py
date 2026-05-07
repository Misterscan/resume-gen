import unittest
from unittest.mock import patch, MagicMock
from services.google_drive import get_gdoc_text, upload_to_gdoc
from services.exceptions import IntegrationError

class TestGoogleDriveService(unittest.TestCase):

    @patch('services.google_drive.build')
    @patch('services.google_drive.MediaIoBaseDownload')
    def test_get_gdoc_text_success(self, mock_download, mock_build):
        mock_drive_service = MagicMock()
        mock_build.return_value = mock_drive_service
        
        mock_request = MagicMock()
        mock_drive_service.files().export_media.return_value = mock_request
        
        # Mock the download iterator to populate the buffer and finish immediately
        def mock_next_chunk():
            mock_download.call_args[0][0].write(b"Test Google Doc Content")
            return MagicMock(), True
            
        mock_downloader_instance = mock_download.return_value
        mock_downloader_instance.next_chunk.side_effect = mock_next_chunk
        
        text = get_gdoc_text("dummy_id", "dummy_creds")
        self.assertEqual(text, "Test Google Doc Content")

    @patch('services.google_drive.build')
    @patch('services.google_drive.MediaFileUpload')
    def test_upload_to_gdoc_create_new(self, mock_media_upload, mock_build):
        mock_drive_service = MagicMock()
        mock_build.return_value = mock_drive_service
        
        mock_files = mock_drive_service.files.return_value
        mock_files.create.return_value.execute.return_value = {"id": "new_id", "webViewLink": "http://link"}
        
        link = upload_to_gdoc("/path/to/file.docx", "file.docx", "dummy_creds", overwrite_id=None)
        
        self.assertEqual(link, "http://link")
        mock_files.create.assert_called_once()
        mock_files.update.assert_not_called()

    @patch('services.google_drive.build')
    @patch('services.google_drive.MediaFileUpload')
    def test_upload_to_gdoc_overwrite_existing(self, mock_media_upload, mock_build):
        mock_drive_service = MagicMock()
        mock_build.return_value = mock_drive_service
        
        mock_files = mock_drive_service.files.return_value
        mock_files.update.return_value.execute.return_value = {"id": "existing_id", "webViewLink": "http://updated_link"}
        
        link = upload_to_gdoc("/path/to/file.docx", "file.docx", "dummy_creds", overwrite_id="existing_id")
        
        self.assertEqual(link, "http://updated_link")
        mock_files.update.assert_called_once()
        mock_files.create.assert_not_called()

if __name__ == '__main__':
    unittest.main()
