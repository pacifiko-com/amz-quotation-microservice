"""AWS Lambda entry point for quotation from a known Amazon USD price."""

from typing import Any

from database.connection import warm_connections
from database.settings_cache import warm_country_settings
from DTO.quotation_request_dto import PriceQuotationRequestDTO
from service.price_quotation_service import PriceQuotationService
from Utils.lambda_entry import handle_quotation_event

# Created once per execution environment and reused by warm invocations.
warm_connections()
warm_country_settings()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process a direct or API Gateway direct-price quotation event.

    Args:
        event: Request containing ``country`` and ``products`` with
            ``amazon_price_usd``. Offers are not accepted or selected.
        context: AWS Lambda runtime context. It is accepted for the required
            handler signature and not used by business logic.

    Returns:
        dict[str, Any]: Always contains ``success``, ``message`` and
            ``result``. Root validation/configuration failures return an
            empty result array.
    """
    return handle_quotation_event(
        event,
        PriceQuotationRequestDTO.from_event,
        lambda request: PriceQuotationService().quote(request),
    )


if __name__ == "__main__":
    result = lambda_handler(
        {
            "body": {
                "country": "CR",
                "products": [
                    {
                        "product_id": 188863,
                        "amz_weight_kg": 1.62,
                        "unspsc": "49221500",
                        "amazon_price_usd": 63.74,
                    }
                ],
            }
        },
        {},
    )
    print(result)
