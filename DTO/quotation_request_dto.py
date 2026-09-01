"""Input DTOs for multi-country quotation requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class RequestValidationError(ValueError):
    """Raised when the Lambda payload does not match the public contract."""


@dataclass(frozen=True)
class ProductQuotationDTO:
    """Amazon and Pacifiko data required to quote one product.

    Attributes:
        product_id: Pacifiko product identifier.
        amz_weight_kg: Raw Amazon item weight in kilograms.
        unspsc: UNSPSC code used to resolve import policy.
        amz_offers: Raw ``includedDataTypes.OFFERS`` items.
        pac_product_weight: Optional weight override in kilograms.
        pac_product_courier: Optional courier override.
        pac_product_partida: Optional tariff code override. It is a string
            so leading zeros are preserved.
    """

    product_id: int
    amz_weight_kg: Decimal
    unspsc: str
    amz_offers: tuple[dict[str, Any], ...]
    pac_product_weight: Decimal | None = None
    pac_product_courier: bool | None = None
    pac_product_partida: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProductQuotationDTO":
        """Create a product DTO from one element of ``products``.

        Args:
            payload: Object containing product identity, Amazon weight,
                UNSPSC, raw offers and optional Pacifiko overrides.

        Returns:
            ProductQuotationDTO: Validated immutable product input.

        Raises:
            RequestValidationError: If a required field is absent or has an
                incompatible shape.
        """
        if not isinstance(payload, dict):
            raise RequestValidationError("Each product must be an object.")

        product_id = payload.get("product_id")
        if not isinstance(product_id, int) or isinstance(product_id, bool):
            raise RequestValidationError("product_id must be an integer.")

        unspsc = str(payload.get("unspsc") or "").strip()
        if not unspsc:
            raise RequestValidationError("unspsc is required.")

        offers = payload.get("amz_offers")
        if not isinstance(offers, list) or any(not isinstance(item, dict) for item in offers):
            raise RequestValidationError("amz_offers must be an array of objects.")

        courier = payload.get("pac_product_courier")
        if courier is not None and not isinstance(courier, bool):
            raise RequestValidationError("pac_product_courier must be boolean or null.")

        partida_value = payload.get("pac_product_partida")
        if partida_value is not None and not isinstance(partida_value, str):
            raise RequestValidationError("pac_product_partida must be a string or null.")
        partida = partida_value.strip() if partida_value is not None else None

        return cls(
            product_id=product_id,
            amz_weight_kg=_required_decimal(payload.get("amz_weight_kg"), "amz_weight_kg"),
            unspsc=unspsc,
            amz_offers=tuple(offers),
            pac_product_weight=_optional_decimal(
                payload.get("pac_product_weight"),
                "pac_product_weight",
            ),
            pac_product_courier=courier,
            pac_product_partida=partida or None,
        )


@dataclass(frozen=True)
class QuotationRequestDTO:
    """Root quotation request.

    Attributes:
        country: Country code supported by the Strategy factory.
        products: Products processed independently in input order.
        prefer_amazon_fulfillment: When true, after the delivery-date pass
            the selector prefers the first eligible Amazon fulfillment offer,
            matching GT detail page / variants. When false or omitted, that
            pass is skipped, matching the GT cotizador.
    """

    country: str
    products: tuple[ProductQuotationDTO, ...]
    prefer_amazon_fulfillment: bool = False

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "QuotationRequestDTO":
        """Build the request from a direct Lambda or API Gateway event.

        Args:
            event: Lambda event. It may contain the contract directly or as
                JSON under an API Gateway ``body`` field.

        Returns:
            QuotationRequestDTO: Validated country and products.

        Raises:
            RequestValidationError: If the root contract is invalid.
        """
        payload = _extract_payload(event)
        country = str(payload.get("country") or "").strip().upper()
        if country not in {"GT", "CR"}:
            raise RequestValidationError("country must be GT or CR.")

        products = payload.get("products")
        if not isinstance(products, list) or not products:
            raise RequestValidationError("products must be a non-empty array.")

        prefer_amazon_fulfillment = payload.get("prefer_amazon_fulfillment")
        if prefer_amazon_fulfillment is None:
            prefer_amazon_fulfillment = False
        elif not isinstance(prefer_amazon_fulfillment, bool):
            raise RequestValidationError(
                "prefer_amazon_fulfillment must be boolean or omitted."
            )

        return cls(
            country=country,
            products=tuple(ProductQuotationDTO.from_dict(item) for item in products),
            prefer_amazon_fulfillment=prefer_amazon_fulfillment,
        )


def _extract_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Extract a JSON object from a Lambda event.

    Args:
        event: Direct request object or API Gateway envelope.

    Returns:
        dict[str, Any]: Unwrapped quotation payload.
    """
    if "body" not in event:
        raise RequestValidationError("The Lambda event must contain a body.")

    body = event["body"]
    if isinstance(body, dict):
        return body
    try:
        parsed = json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RequestValidationError("body must contain a valid JSON object.") from exc
    if not isinstance(parsed, dict):
        raise RequestValidationError("body must contain a JSON object.")
    return parsed


def _required_decimal(value: Any, field_name: str) -> Decimal:
    """Parse a required non-negative decimal field.

    Args:
        value: Raw JSON value.
        field_name: Public field name used in validation messages.

    Returns:
        Decimal: Exact non-negative numeric value.
    """
    parsed = _optional_decimal(value, field_name)
    if parsed is None:
        raise RequestValidationError(f"{field_name} is required.")
    return parsed


def _optional_decimal(value: Any, field_name: str) -> Decimal | None:
    """Parse an optional non-negative decimal field.

    Args:
        value: Raw JSON value or ``None``.
        field_name: Public field name used in validation messages.

    Returns:
        Decimal | None: Exact value, or ``None`` when absent.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise RequestValidationError(f"{field_name} must be numeric.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RequestValidationError(f"{field_name} must be numeric.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise RequestValidationError(f"{field_name} must be a non-negative number.")
    return parsed
