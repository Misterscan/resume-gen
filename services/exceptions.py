class ResumeGenException(Exception):
    """Base exception for Resume Gen."""
    pass

class ConfigurationError(ResumeGenException):
    """Raised when environment variables or configs are missing or invalid."""
    pass

class IntegrationError(ResumeGenException):
    """Raised when a third-party API integration fails (e.g. Google Drive, Gemini)."""
    pass

class DocumentError(ResumeGenException):
    """Raised when a document generation or parsing fails."""
    pass

class ValidationError(ResumeGenException):
    """Raised when a schema validation fails."""
    pass
