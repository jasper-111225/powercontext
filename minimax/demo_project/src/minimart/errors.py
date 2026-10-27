"""Domain errors for the MiniMart order service."""


class ValidationError(ValueError):
    """Raised when a request violates a documented requirement (REQ-001, REQ-002)."""
