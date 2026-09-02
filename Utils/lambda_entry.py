"""Shared AWS Lambda request handling for quotation endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from database.connection import ensure_connection
from DTO.quotation_request_dto import RequestValidationError
from DTO.quotation_response_dto import QuotationResponseDTO
from Utils.logger import get_logger

logger = get_logger(__name__)


def handle_quotation_event(
    event: dict[str, Any],
    parse_request: Callable[[dict[str, Any]], Any],
    quote: Callable[[Any], QuotationResponseDTO],
) -> dict[str, Any]:
    """Parse a Lambda event, quote it and serialize the response.

    Args:
        event: Direct or API Gateway quotation event.
        parse_request: Endpoint-specific request validator.
        quote: Endpoint-specific quotation service call.

    Returns:
        dict[str, Any]: Always contains ``success``, ``message`` and
            ``result``. Root validation/configuration failures return an
            empty result array.
    """
    try:
        request = parse_request(event)
        ensure_connection(request.country)
        return quote(request).to_dict()
    except (RequestValidationError, ValueError) as exc:
        logger.warning("Quotation request rejected: %s", exc)
        return _failed_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected quotation failure.")
        return _failed_response(_unexpected_error_message(exc))


def _failed_response(message: str) -> dict[str, Any]:
    """Build a root-level failed response.

    Args:
        message: Safe explanation returned to the caller.

    Returns:
        dict[str, Any]: Failed response with an empty product list.
    """
    return QuotationResponseDTO(success=False, message=message, result=()).to_dict()


def _unexpected_error_message(exc: BaseException) -> str:
    """Build a response message that includes the unexpected error.

    Args:
        exc: Exception that escaped the quotation flow.

    Returns:
        str: Generic prefix plus exception type and detail.
    """
    detail = str(exc).strip()
    name = type(exc).__name__
    if detail:
        return f"Unexpected quotation failure: {name}: {detail}"
    return f"Unexpected quotation failure: {name}"
