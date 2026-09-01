"""AWS Lambda entry point for multi-country quotation."""

from typing import Any

from database.connection import ensure_connection, warm_connections
from DTO.quotation_request_dto import QuotationRequestDTO, RequestValidationError
from DTO.quotation_response_dto import QuotationResponseDTO
from service.quotation_service import QuotationService
from Utils.logger import get_logger

logger = get_logger(__name__)

# Created once per execution environment and reused by warm invocations.
warm_connections()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process a direct or API Gateway quotation event.

    Args:
        event: Request containing ``country``, optional
            ``prefer_amazon_fulfillment`` and ``products``.
        context: AWS Lambda runtime context. It is accepted for the required
            handler signature and not used by business logic.

    Returns:
        dict[str, Any]: Always contains ``success``, ``message`` and
            ``result``. Root validation/configuration failures return an
            empty result array.
    """
    try:
        request = QuotationRequestDTO.from_event(event)
        ensure_connection(request.country)
        return QuotationService().quote(request).to_dict()
    except (RequestValidationError, ValueError) as exc:
        logger.warning("Quotation request rejected: %s", exc)
        return _failed_response(str(exc))
    except Exception:
        logger.exception("Unexpected quotation failure.")
        return _failed_response("Unexpected quotation failure.")


def _failed_response(message: str) -> dict[str, Any]:
    """Build a root-level failed response.

    Args:
        message: Safe explanation returned to the caller.

    Returns:
        dict[str, Any]: Failed response with an empty product list.
    """
    return QuotationResponseDTO(success=False, message=message, result=()).to_dict()


if __name__ == "__main__":
    res = lambda_handler({
        "body": {
            "country": "CR",
            "products": [
                {
                    "product_id": 188863,
                    "amz_weight_kg": 1.62,
                    "unspsc": "49221500",
                    "amz_offers":[
                    
                ] ,
                }
            ]
        }
    }, {})
    print(res)