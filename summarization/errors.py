"""Lightweight exception types shared by summary execution boundaries."""


class ModelUnavailableError(RuntimeError):
    """Raised when the configured summary-model provider cannot be used."""


class SummaryFailedError(RuntimeError):
    """Raised when summary processing fails for a non-provider reason."""


__all__ = ["ModelUnavailableError", "SummaryFailedError"]
