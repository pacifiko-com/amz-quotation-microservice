"""Output DTOs for the Lambda quotation contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ResultObjectDTO:
    """Quotation result for one input product.

    Attributes:
        success: Whether this product was quoted successfully.
        message: Product-level response exposed as ``Message``.
        product_id: Pacifiko product identifier from the request.
        offer_id: Identifier of the selected Amazon offer.
        amazon_price: Public Amazon USD amount. List price when a special
            qualifies; otherwise the selected offer.
        special_amazon_price: Selected Amazon offer USD when a special
            qualifies; otherwise ``None``.
        price_dolar: Quoted sale price in USD from the country calculator,
            before local rounding.
        price_local: Quoted sale price in local currency. When a special
            qualifies this is the list calculation; otherwise the offer
            calculation.
        special_price_dolar: Quoted special sale price in USD from the
            calculator, or ``None``.
        special_price_local: Quoted special sale price in local currency
            when a special qualifies; otherwise ``None``.
        cost_dolar: Landed cost in USD.
        cost_local: Landed cost converted with the product exchange rate.
        exchange_rate: USD-to-local rate used for this product.
        currency_code: Local ISO currency code from ``oc_setting``.
        delivery_promise_amz: Country delivery promise tier.
        courier: Resolved courier flag.
        restriction: Resolved import restriction.
        partida: Resolved tariff code, when available.
    """

    success: bool
    message: str
    product_id: int
    offer_id: str = ""
    amazon_price: Decimal | None = None
    special_amazon_price: Decimal | None = None
    price_dolar: Decimal | None = None
    price_local: Decimal | None = None
    special_price_dolar: Decimal | None = None
    special_price_local: Decimal | None = None
    cost_dolar: Decimal | None = None
    cost_local: Decimal | None = None
    exchange_rate: Decimal | None = None
    currency_code: str | None = None
    delivery_promise_amz: int | None = None
    courier: bool | None = None
    restriction: int | None = None
    partida: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the product result using the public field names.

        Returns:
            dict[str, Any]: JSON-ready result object. Monetary values are
                emitted as numbers while ``Message`` preserves required case.
        """
        return {
            "product_id": self.product_id,
            "offer_id": self.offer_id,
            "amazon_price": _number(self.amazon_price),
            "special_amazon_price": _number(self.special_amazon_price),
            "price_dolar": _number(self.price_dolar),
            "price_local": _number(self.price_local),
            "special_price_dolar": _number(self.special_price_dolar),
            "special_price_local": _number(self.special_price_local),
            "cost_dolar": _number(self.cost_dolar),
            "cost_local": _number(self.cost_local),
            "exchange_rate": _number(self.exchange_rate),
            "currency_code": self.currency_code,
            "delivery_promise_amz": self.delivery_promise_amz,
            "courier": self.courier,
            "restriction": self.restriction,
            "partida": self.partida,
            "success": self.success,
            "Message": self.message,
        }


@dataclass(frozen=True)
class QuotationResponseDTO:
    """General result for a quotation request.

    Attributes:
        success: True only when every product was quoted successfully.
        message: Summary of complete, partial or failed processing.
        result: One result per input product, in the original order.
    """

    success: bool
    message: str
    result: tuple[ResultObjectDTO, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the response envelope.

        Returns:
            dict[str, Any]: Public ``success``, ``message`` and ``result``.
        """
        return {
            "success": self.success,
            "message": self.message,
            "result": [item.to_dict() for item in self.result],
        }


def _number(value: Decimal | None) -> float | None:
    """Convert a Decimal to a JSON number.

    Args:
        value: Monetary amount or ``None`` for failed products.

    Returns:
        float | None: JSON-compatible representation.
    """
    return float(value) if value is not None else None
