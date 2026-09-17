"""Domain exceptions for controlled quotation failures."""


class QuotationError(Exception):
    """Base error with a stable response message."""


class ProductQuotationError(QuotationError):
    """Raised when one product cannot be quoted."""


class ConfigurationError(QuotationError):
    """Raised when deployment or country settings are incomplete."""
