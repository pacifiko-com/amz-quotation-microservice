"""Shared AWS Lambda request handling for quotation endpoints."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from database.connection import ensure_connection, warm_connections
from database.settings_cache import warm_country_settings
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
        dict[str, Any]: Direct invokes return ``success``, ``message`` and
            ``result``. API Gateway proxy events wrap that payload in
            ``statusCode``, ``headers`` and a JSON ``body``.
    """
    try:
        request = _prepare_and_parse(event, parse_request)
        ensure_connection(request.country)
        payload = quote(request).to_dict()
        return _format_response(event, payload, 200)
    except (RequestValidationError, ValueError) as exc:
        logger.warning("Quotation request rejected: %s", exc)
        return _format_response(event, _failed_response(str(exc)), 400)
    except Exception as exc:
        correlation_id = uuid.uuid4().hex
        logger.exception(
            "Unexpected quotation failure. correlation_id=%s",
            correlation_id,
            exc_info=exc,
        )
        return _format_response(
            event,
            _failed_response(_unexpected_error_message()),
            500,
        )


def _prepare_and_parse(
    event: dict[str, Any],
    parse_request: Callable[[dict[str, Any]], Any],
) -> Any:
    """Warm process caches once, then validate the Lambda event.

    Args:
        event: Direct or API Gateway quotation event.
        parse_request: Endpoint-specific request validator.

    Returns:
        Any: Parsed request DTO for the current endpoint.
    """
    _warm_once()
    return parse_request(event)


@lru_cache(maxsize=1)
def _warm_once() -> None:
    """Open reused connections and load country settings on first request."""
    warm_connections()
    warm_country_settings()


def _format_response(
    event: dict[str, Any],
    payload: dict[str, Any],
    status_code: int,
) -> dict[str, Any]:
    """Return a direct Lambda payload or an API Gateway proxy response.

    Args:
        event: Original Lambda event.
        payload: Public quotation JSON object.
        status_code: HTTP status used only for API Gateway proxy events.

    Returns:
        dict[str, Any]: Unwrapped payload, or a Lambda proxy response.
    """
    if not _is_api_gateway_event(event):
        return payload
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _is_api_gateway_event(event: dict[str, Any]) -> bool:
    """Detect REST API Gateway proxy events.

    Args:
        event: Original Lambda event.

    Returns:
        bool: True when API Gateway supplied a ``requestContext`` object.
    """
    return isinstance(event.get("requestContext"), dict)


def _failed_response(message: str) -> dict[str, Any]:
    """Build a root-level failed response.

    Args:
        message: Safe explanation returned to the caller.

    Returns:
        dict[str, Any]: Failed response with an empty product list.
    """
    return QuotationResponseDTO(success=False, message=message, result=()).to_dict()


def _unexpected_error_message() -> str:
    """Return the public message for an unexpected quotation failure.

    Returns:
        str: Fixed generic message with no exception type or detail.
    """
    return "Unexpected quotation failure."
