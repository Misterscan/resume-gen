import unittest
from unittest.mock import patch, MagicMock
from services.llm import generate_resume_content, revise_resume_content, generate_cover_letter_content
from services.exceptions import IntegrationError, ValidationError

class TestLLMService(unittest.TestCase):
    
    @patch('services.llm.genai.Client')
    def test_generate_resume_content_success(self, mock_genai_client):
        # Setup mock return from Gemini
        mock_response = MagicMock()
        mock_response.text = '''
        {
            "professional_summary": "Test Summary",
            "work_experience": [],
            "education": [],
            "skills": ["Python"]
        }
        '''
        mock_client_instance = mock_genai_client.return_value
        mock_client_instance.models.generate_content.return_value = mock_response

        raw_data = {"full_name": "John Doe", "contact_info": "john@example.com"}
        result = generate_resume_content(raw_data=raw_data, api_key="dummy_key")

        self.assertEqual(result["professional_summary"], "Test Summary")
        self.assertIn("Python", result["skills"])
        mock_client_instance.models.generate_content.assert_called_once()

    @patch('services.llm.genai.Client')
    def test_generate_resume_schema_validation_failure(self, mock_genai_client):
        # Setup mock return missing required schema fields
        mock_response = MagicMock()
        mock_response.text = '{"bad_json_structure"}' # purposefully bad to trigger JSON failure or validation
        mock_client_instance = mock_genai_client.return_value
        mock_client_instance.models.generate_content.return_value = mock_response

        raw_data = {"full_name": "John Doe"}
        
        with self.assertRaises((ValidationError, IntegrationError)):
            generate_resume_content(raw_data=raw_data, api_key="dummy_key")

    @patch('services.llm.genai.Client')
    def test_revise_resume_content_success(self, mock_genai_client):
        mock_response = MagicMock()
        mock_response.text = '''
        {
            "candidate": {
                "full_name": "Test User",
                "contact_info": "new@example.com"
            },
            "resume": {
                "professional_summary": "Revised Summary",
                "work_experience": [],
                "education": [],
                "skills": ["Python", "Django"]
            }
        }
        '''
        mock_client_instance = mock_genai_client.return_value
        mock_client_instance.models.generate_content.return_value = mock_response

        result = revise_resume_content(
            api_key="dummy_key",
            revision_notes="Add Django to skills",
            current_resume={"professional_summary": "Old Summary"}
        )

        self.assertEqual(result["resume"]["professional_summary"], "Revised Summary")
        self.assertIn("Django", result["resume"]["skills"])

    @patch('services.llm.genai.Client')
    def test_generate_cover_letter_content(self, mock_genai_client):
        mock_response = MagicMock()
        mock_response.text = '''
        {
            "recipient_info": "Hiring Team",
            "greeting": "Hello,",
            "introduction": "Intro",
            "body_paragraphs": ["Body 1"],
            "company_connection": "Connection",
            "closing": "Closing",
            "sign_off": "Best"
        }
        '''
        mock_client_instance = mock_genai_client.return_value
        mock_client_instance.models.generate_content.return_value = mock_response

        result = generate_cover_letter_content(
            raw_data={},
            resume={},
            revision_notes="",
            target_role="Developer",
            target_company="Acme Corp",
            api_key="dummy_key"
        )
        
        self.assertEqual(result["recipient_info"], "Hiring Team")
        self.assertEqual(result["introduction"], "Intro")

if __name__ == '__main__':
    unittest.main()
