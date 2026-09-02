"""AWS Lambda entry point for multi-country quotation."""

from typing import Any

from database.connection import ensure_connection, warm_connections
from database.settings_cache import warm_country_settings
from DTO.quotation_request_dto import QuotationRequestDTO, RequestValidationError
from DTO.quotation_response_dto import QuotationResponseDTO
from service.quotation_service import QuotationService
from Utils.logger import get_logger

logger = get_logger(__name__)

# Created once per execution environment and reused by warm invocations.
warm_connections()
warm_country_settings()


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
                    {
                        "availability": "In Stock.",
                        "buyingGuidance": "NONE",
                        "buyingGuidanceV2": {
                            "buyingGuidance": [
                                {
                                    "title": "NONE",
                                    "message": None,
                                    "link": None,
                                    "type": "NONE",
                                    "fragments": []
                                }
                            ]
                        },
                        "buyingRestrictions": [],
                        "fulfillmentType": "AMAZON_FULFILLMENT",
                        "merchant": {
                            "merchantId": None,
                            "name": "Spikeball",
                            "meanFeedbackRating": None,
                            "totalFeedbackCount": None,
                            "certificates": [
                                " Certificación 889"
                            ]
                        },
                        "fulfiller": {
                            "name": "Amazon",
                            "fulfillmentType": "AMAZON_FULFILLMENT"
                        },
                        "offerId": "VCoCbtBLr1ypIDTsosHkfDWySb5o36DcHVWcB4A5hVmSU2gyfnkUZHSbugRxMVarWk%2FaYkSIhw3Cay8%2FDrqSqz%2B4mzZMtAVsCCFx%2Fgz%2BsEguBAYfIzWMp2TAZUwfgW6Em7U73Gu4zNFkZyl7%2Fy4xlN1b7Ti0pNOQEOwFJVy15uKwSouZmOuonw%3D%3D",
                        "price": {
                            "value": {
                                "amount": 63.74,
                                "currencyCode": "USD"
                            },
                            "formattedPrice": None,
                            "priceType": "SALE"
                        },
                        "shippingOptions": [
                            {
                                "shippingCost": {
                                    "value": {
                                        "amount": 0.0,
                                        "currencyCode": "USD"
                                    }
                                },
                                "deliveryRange": {
                                    "max": "2026-09-02T12:00:00Z",
                                    "min": "2026-09-02T08:00:00Z"
                                },
                                "deliveryInformation": "Entrega GRATIS mañana entre las 4:00 - 8:00",
                                "thresholdCost": None
                            }
                        ],
                        "badges": [
                            {
                                "fragment": {
                                    "title": "Amazon Prime",
                                    "message": None,
                                    "link": None,
                                    "type": "PRIME_BADGE",
                                    "fragments": None
                                }
                            },
                            {
                                "fragment": {
                                    "title": "Factura con IVA descargable",
                                    "message": "Vendedores empresariales: ",
                                    "link": None,
                                    "type": "BUSINESS_INVOICE_BADGE",
                                    "fragments": None
                                }
                            }
                        ],
                        "quantityInStock": None,
                        "listPrice": {
                            "value": {
                                "amount": 74.99,
                                "currencyCode": "USD"
                            },
                            "formattedPrice": None,
                            "priceType": "LIST_PRICE"
                        },
                        "productCondition": "NEW",
                        "productConditionNote": "",
                        "condition": {
                            "conditionValue": "NEW",
                            "conditionNote": "",
                            "subCondition": "UNKNOWN"
                        },
                        "quantityLimits": {
                            "maxQuantity": 30,
                            "minQuantity": 1
                        },
                        "quantityPrice": {
                            "quantityPriceTiers": []
                        },
                        "taxExclusivePrice": {
                            "taxExclusiveAmount": None,
                            "displayString": "",
                            "formattedPrice": "",
                            "label": ""
                        },
                        "deliveryInformation": "Entrega GRATIS mañana entre las 4:00 - 8:00",
                        "dealInformation": None
                    }
                ] ,
                }
            ]
        }
    }, {})
    print(res)