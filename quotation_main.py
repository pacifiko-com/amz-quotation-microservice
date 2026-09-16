"""AWS Lambda entry point for multi-country quotation with offer selection."""

from typing import Any

from DTO.quotation_request_dto import QuotationRequestDTO
from service.quotation_service import QuotationService
from Utils.lambda_entry import handle_quotation_event


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process a direct or API Gateway offer-selection quotation event.

    Args:
        event: Request containing ``country``, optional
            ``prefer_amazon_fulfillment`` and ``products`` with ``amz_offers``.
        context: AWS Lambda runtime context. It is accepted for the required
            handler signature and not used by business logic.

    Returns:
        dict[str, Any]: Direct invokes return ``success``, ``message`` and
            ``result``. API Gateway proxy events wrap that payload in
            ``statusCode``, ``headers`` and a JSON ``body``. Root
            validation/configuration failures return an empty result array.
    """
    return handle_quotation_event(
        event,
        QuotationRequestDTO.from_event,
        lambda request: QuotationService().quote(request),
    )


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