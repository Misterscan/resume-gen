import unittest
import io
from docx import Document

from services.document import (
    extract_docx_text,
    generate_docx_stream,
    generate_cover_letter_docx_stream
)
from services.exceptions import DocumentError

class TestDocumentService(unittest.TestCase):

    def setUp(self):
        self.mock_resume_data = {
            "professional_summary": "Experienced software engineer.",
            "work_experience": [
                {
                    "title": "Senior Engineer",
                    "company": "Tech Corp",
                    "location": "Remote",
                    "dates": "Jan 2020 - Present",
                    "bullets": ["Optimized backend.", "Led team."]
                }
            ],
            "education": [
                {
                    "degree": "B.S. Computer Science",
                    "institution": "University",
                    "location": "City, State",
                    "dates": "2015 - 2019",
                    "details": "GPA: 4.0"
                }
            ],
            "skills": ["Python", "Django", "TDD"]
        }
        self.mock_raw_data = {
            "full_name": "Test User",
            "contact_info": "test@example.com | 555-5555"
        }
        self.mock_cover_letter_data = {
            "recipient_info": "Hiring Manager, Tech Corp",
            "greeting": "Dear Hiring Manager,",
            "introduction": "I am writing to apply for the position.",
            "body_paragraphs": ["I have 5 years of Python experience."],
            "company_connection": "I love Tech Corp's mission.",
            "closing": "I look forward to an interview.",
            "sign_off": "Sincerely,"
        }

    def test_generate_docx_stream_returns_valid_bytesio(self):
        result_stream = generate_docx_stream(self.mock_resume_data, self.mock_raw_data)
        
        # Verify it returns a byte stream
        self.assertIsInstance(result_stream, io.BytesIO)
        
        # Verify the stream is readable by python-docx
        doc = Document(result_stream)
        text = "\n".join([p.text for p in doc.paragraphs])
        
        self.assertIn("Test User", text)
        self.assertIn("test@example.com | 555-5555", text)
        self.assertIn("PROFESSIONAL SUMMARY", text)
        self.assertIn("Experienced software engineer", text)

    def test_generate_cover_letter_docx_stream_returns_valid_bytesio(self):
        result_stream = generate_cover_letter_docx_stream(self.mock_cover_letter_data, self.mock_raw_data)
        
        self.assertIsInstance(result_stream, io.BytesIO)
        
        doc = Document(result_stream)
        text = "\n".join([p.text for p in doc.paragraphs])
        
        self.assertIn("Test User", text)
        self.assertIn("test@example.com | 555-5555", text)
        self.assertIn("Dear Hiring Manager,", text)
        self.assertIn("Sincerely,", text)

    def test_extract_docx_text_valid_file(self):
        # Create a mock docx in memory
        temp_doc = Document()
        temp_doc.add_paragraph("First paragraph text.")
        temp_doc.add_paragraph("Second paragraph text.")
        stream = io.BytesIO()
        temp_doc.save(stream)
        stream.seek(0)
        
        extracted_text = extract_docx_text(stream)
        self.assertEqual(extracted_text, "First paragraph text.\nSecond paragraph text.")

    def test_extract_docx_text_empty_file_raises_error(self):
        temp_doc = Document()
        stream = io.BytesIO()
        temp_doc.save(stream)
        stream.seek(0)
        
        with self.assertRaises(DocumentError):
            extract_docx_text(stream)

if __name__ == '__main__':
    unittest.main()
