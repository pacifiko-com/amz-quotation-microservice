"""Input DTOs for multi-country quotation requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from const import COUNTRIES, KG_TO_LB, OZ_PER_KG


class RequestValidationError(ValueError):
    """Raised when the Lambda payload does not match the public contract."""


@dataclass(frozen=True, kw_only=True)
class ProductFactsDTO:
    """Shared Amazon and Pacifiko fields used to resolve import policy.

    Attributes:
        product_id: Pacifiko product identifier, or ``None`` when the
            product is not yet stored in ``oc_product``.
        amz_weight_kg: Amazon item weight in kilograms after converting
            ``amz_weight_kg``, ``amz_weight_lb`` or ``amz_weight_oz``.
        unspsc: UNSPSC code used to resolve import policy, or ``None``
            to apply country defaults without recording an unknown code.
        pac_product_weight: Optional Pacifiko weight override in pounds.
        pac_product_courier: Optional courier override.
        pac_product_partida: Optional tariff code override. It is a string
            so leading zeros are preserved.
    """

    product_id: int | None
    amz_weight_kg: Decimal
    unspsc: str | None
    pac_product_weight: Decimal | None = None
    pac_product_courier: bool | None = None
    pac_product_partida: str | None = None


@dataclass(frozen=True, kw_only=True)
class ProductQuotationDTO(ProductFactsDTO):
    """Product input for the offer-selection quotation endpoint.

    Attributes:
        amz_offers: Raw ``includedDataTypes.OFFERS`` items.
    """

    amz_offers: tuple[dict[str, Any], ...]

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
        fields = _product_facts_fields(payload)
        offers = payload.get("amz_offers")
        if not isinstance(offers, list) or any(
            not isinstance(item, dict) for item in offers
        ):
            raise RequestValidationError("amz_offers must be an array of objects.")
        return cls(**fields, amz_offers=tuple(offers))


@dataclass(frozen=True, kw_only=True)
class PriceQuotationProductDTO(ProductFactsDTO):
    """Product input for the direct Amazon-USD quotation endpoint.

    Attributes:
        amazon_price_usd: Amazon amount in USD used as the calculator input.
    """

    amazon_price_usd: Decimal

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PriceQuotationProductDTO":
        """Create a product DTO from one element of ``products``.

        Args:
            payload: Object containing product identity, Amazon weight,
                UNSPSC, Amazon USD price and optional Pacifiko overrides.

        Returns:
            PriceQuotationProductDTO: Validated immutable product input.

        Raises:
            RequestValidationError: If a required field is absent or has an
                incompatible shape.
        """
        fields = _product_facts_fields(payload)
        return cls(
            **fields,
            amazon_price_usd=_required_decimal(
                payload.get("amazon_price_usd"),
                "amazon_price_usd",
            ),
        )


@dataclass(frozen=True)
class QuotationRequestDTO:
    """Root request for quotation with Amazon offer selection.

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
        """Build the offer-selection request from a Lambda event.

        Args:
            event: Lambda event. It may contain the contract directly or as
                JSON under an API Gateway ``body`` field.

        Returns:
            QuotationRequestDTO: Validated country, products and AF flag.

        Raises:
            RequestValidationError: If the root contract is invalid.
        """
        payload = _extract_payload(event)
        prefer_amazon_fulfillment = payload.get("prefer_amazon_fulfillment")
        if prefer_amazon_fulfillment is None:
            prefer_amazon_fulfillment = False
        elif not isinstance(prefer_amazon_fulfillment, bool):
            raise RequestValidationError(
                "prefer_amazon_fulfillment must be boolean or omitted."
            )
        country, products = _country_and_products(payload)
        return cls(
            country=country,
            products=tuple(ProductQuotationDTO.from_dict(item) for item in products),
            prefer_amazon_fulfillment=prefer_amazon_fulfillment,
        )


@dataclass(frozen=True)
class PriceQuotationRequestDTO:
    """Root request for quotation from a known Amazon USD price.

    Attributes:
        country: Country code supported by the Strategy factory.
        products: Products processed independently in input order.
    """

    country: str
    products: tuple[PriceQuotationProductDTO, ...]

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "PriceQuotationRequestDTO":
        """Build the direct-price request from a Lambda event.

        Args:
            event: Lambda event. It may contain the contract directly or as
                JSON under an API Gateway ``body`` field.

        Returns:
            PriceQuotationRequestDTO: Validated country and products.

        Raises:
            RequestValidationError: If the root contract is invalid.
        """
        country, products = _country_and_products(_extract_payload(event))
        return cls(
            country=country,
            products=tuple(
                PriceQuotationProductDTO.from_dict(item) for item in products
            ),
        )


def _country_and_products(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    """Validate shared root fields used by both quotation endpoints.

    Args:
        payload: Unwrapped JSON object from the Lambda event.

    Returns:
        tuple[str, list[Any]]: Uppercase country code and products array.
    """
    country = str(payload.get("country") or "").strip().upper()
    if country not in COUNTRIES:
        raise RequestValidationError(
            f"country must be {' or '.join(COUNTRIES)}."
        )

    products = payload.get("products")
    if not isinstance(products, list) or not products:
        raise RequestValidationError("products must be a non-empty array.")
    return country, products


def _product_facts_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse the policy-resolution fields shared by both product contracts.

    Args:
        payload: One element of the ``products`` array.

    Returns:
        dict[str, Any]: Keyword arguments for ``ProductFactsDTO``.
    """
    if not isinstance(payload, dict):
        raise RequestValidationError("Each product must be an object.")

    if "product_id" not in payload:
        raise RequestValidationError("product_id is required.")

    product_id = payload["product_id"]
    if product_id is not None and (
        not isinstance(product_id, int) or isinstance(product_id, bool)
    ):
        raise RequestValidationError("product_id must be an integer or null.")

    unspsc = _optional_unspsc(payload.get("unspsc"))

    courier = payload.get("pac_product_courier")
    if courier is not None and not isinstance(courier, bool):
        raise RequestValidationError("pac_product_courier must be boolean or null.")

    partida_value = payload.get("pac_product_partida")
    if partida_value is not None and not isinstance(partida_value, str):
        raise RequestValidationError("pac_product_partida must be a string or null.")
    partida = partida_value.strip() if partida_value is not None else None

    return {
        "product_id": product_id,
        "amz_weight_kg": _resolve_amz_weight_kg(payload),
        "unspsc": unspsc,
        "pac_product_weight": _optional_decimal(
            payload.get("pac_product_weight"),
            "pac_product_weight",
        ),
        "pac_product_courier": courier,
        "pac_product_partida": partida or None,
    }


def _resolve_amz_weight_kg(payload: dict[str, Any]) -> Decimal:
    """Convert the exclusive Amazon weight field to kilograms.

    Args:
        payload: One element of the ``products`` array.

    Returns:
        Decimal: Weight in kilograms used by all downstream calculations.
    """
    weight_kg = _optional_decimal(payload.get("amz_weight_kg"), "amz_weight_kg")
    weight_lb = _optional_decimal(payload.get("amz_weight_lb"), "amz_weight_lb")
    weight_oz = _optional_decimal(payload.get("amz_weight_oz"), "amz_weight_oz")
    provided = [
        name
        for name, value in (
            ("amz_weight_kg", weight_kg),
            ("amz_weight_lb", weight_lb),
            ("amz_weight_oz", weight_oz),
        )
        if value is not None
    ]
    if len(provided) > 1:
        raise RequestValidationError(
            "Only one of amz_weight_kg, amz_weight_lb or amz_weight_oz may be provided."
        )
    if not provided:
        raise RequestValidationError(
            "One of amz_weight_kg, amz_weight_lb or amz_weight_oz is required."
        )
    if weight_lb is not None:
        return weight_lb / KG_TO_LB
    if weight_oz is not None:
        return weight_oz / OZ_PER_KG
    assert weight_kg is not None
    return weight_kg

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


def _optional_unspsc(value: Any) -> str | None:
    """Parse UNSPSC as a non-empty string or JSON null.

    Args:
        value: Raw JSON value.

    Returns:
        str | None: Trimmed code, or ``None`` when the caller omits policy.

    Raises:
        RequestValidationError: If the value is empty, blank or not a string.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestValidationError("unspsc must be a string or null.")
    unspsc = value.strip()
    if not unspsc:
        raise RequestValidationError("unspsc must not be empty.")
    return unspsc


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
