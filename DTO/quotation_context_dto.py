"""Internal DTOs exchanged by Strategies, services and repositories."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ProductDataDTO:
    """Pacifiko product data used as fallback when overrides are absent."""

    weight_lb: Decimal | None
    courier: bool | None
    partida: str | None
    cabys: str | None = None


@dataclass(frozen=True)
class UnspscDataDTO:
    """Import policy stored for one UNSPSC code."""

    arancel_percentage: Decimal
    restriction: int
    category_code: int
    margin_percentage: Decimal
    danger_good_active: bool
    courier: bool


@dataclass(frozen=True)
class TariffDataDTO:
    """Country-neutral shape for a resolved tariff-code row."""

    partida: str
    arancel_percentage: Decimal | None = None
    dai: Decimal | None = None
    isc: Decimal | None = None
    courier: bool | None = None
    restriction: int | None = None
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CountrySettingsDTO:
    """Typed view of the required ``global_store`` settings."""

    values: dict[str, str]

    def require(self, key: str) -> str:
        """Return a required setting value.

        Args:
            key: Exact ``oc_setting.key`` name.

        Returns:
            str: Stored setting value.

        Raises:
            ValueError: If the key is missing or blank.
        """
        value = self.values.get(key)
        if value is None or value == "":
            raise ValueError(f"Missing required oc_setting key: {key}.")
        return value

    def decimal(self, key: str) -> Decimal:
        """Return a required setting as Decimal.

        Args:
            key: Exact ``oc_setting.key`` name.

        Returns:
            Decimal: Finite numeric setting.
        """
        value = self.require(key)
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"oc_setting key {key} must be numeric.") from exc
        if not parsed.is_finite():
            raise ValueError(f"oc_setting key {key} must be finite.")
        return parsed

    def integer(self, key: str) -> int:
        """Return a required setting as integer.

        Args:
            key: Exact ``oc_setting.key`` name.

        Returns:
            int: Parsed integer setting.
        """
        value = self.require(key)
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"oc_setting key {key} must be an integer.") from exc

    def boolean(self, key: str) -> bool:
        """Return a required zero/one setting as boolean.

        Args:
            key: Exact ``oc_setting.key`` name.

        Returns:
            bool: True for ``1``, false for ``0``.
        """
        value = self.require(key)
        if value not in {"0", "1"}:
            raise ValueError(f"oc_setting key {key} must be 0 or 1.")
        return value == "1"

    def string_list(self, key: str) -> tuple[str, ...]:
        """Return a required JSON array of strings.

        Args:
            key: Exact ``oc_setting.key`` name.

        Returns:
            tuple[str, ...]: Normalized non-empty string values.
        """
        import json

        try:
            values = json.loads(self.require(key))
        except json.JSONDecodeError as exc:
            raise ValueError(f"oc_setting key {key} must be a JSON array.") from exc
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"oc_setting key {key} must be a JSON string array.")
        return tuple(item.strip() for item in values if item.strip())


@dataclass(frozen=True)
class QuotationLookupDTO:
    """Batched MySQL rows for one quotation request.

    Attributes:
        products: ``oc_product`` rows keyed by product identifier.
        unspsc: UNSPSC policy keyed by code. ``None`` means the code is new.
        tariffs: Tariff rows keyed by national code.
        category_courier: Category-tree courier flags for remaining products.
    """

    products: dict[int, ProductDataDTO]
    unspsc: dict[str, UnspscDataDTO | None]
    tariffs: dict[str, TariffDataDTO]
    category_courier: dict[int, bool]
    sales_iva: dict[str, Decimal]


EMPTY_PRODUCT_DATA = ProductDataDTO(weight_lb=None, courier=None, partida=None)


@dataclass(frozen=True)
class SelectedOfferDTO:
    """Canonical fields extracted from a variable Amazon offer."""

    offer_id: str
    price_usd: Decimal
    list_price_usd: Decimal | None
    shipping_usd: Decimal
    fulfillment_type: str
    availability: str
    delivery_information: str
    delivery_range_max: str | None
    buying_guidance: str = ""
    buying_guidance_title: str = ""
    buying_guidance_type: str = ""
    max_quantity: int | None = None
    selection_reason: str = ""
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedPolicyDTO:
    """Country policy selected from product, UNSPSC and tariff sources."""

    weight_lb: Decimal
    arancel_percentage: Decimal
    margin_percentage: Decimal
    courier: bool
    restriction: int
    danger_good_active: bool
    partida: str | None
    sales_iva_rate: Decimal
    cabys: str | None = None
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedPricesDTO:
    """Quoted Amazon and sale amounts after offer-versus-list resolution.

    Attributes:
        amazon_price: Public Amazon USD amount. List price when a special
            qualifies; otherwise the selected offer.
        special_amazon_price: Selected Amazon offer USD when a special
            qualifies; otherwise ``None``.
        cost_usd: Landed cost in USD from the offer calculation.
        price_usd: Quoted public sale price in USD from the calculator.
        price_local: Quoted public sale price in local currency.
        price_without_tax_usd: Sale price in USD taken from the calculator
            before sales VAT is applied.
        price_without_tax_local: Sale price in local currency taken from the
            calculator before sales VAT is applied.
        special_price_usd: Quoted special sale price in USD, or ``None``.
        special_price_local: Quoted special sale price in local currency,
            or ``None``.
        special_price_without_tax_usd: Special sale price in USD before
            sales VAT, or ``None``.
        special_price_without_tax_local: Special sale price in local currency
            before sales VAT, or ``None``.
    """

    amazon_price: Decimal
    special_amazon_price: Decimal | None
    cost_usd: Decimal
    price_usd: Decimal
    price_local: Decimal
    price_without_tax_usd: Decimal
    price_without_tax_local: Decimal
    special_price_usd: Decimal | None
    special_price_local: Decimal | None
    special_price_without_tax_usd: Decimal | None
    special_price_without_tax_local: Decimal | None
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalculationResultDTO:
    """Pure calculator output for one Amazon USD price."""

    cost_usd: Decimal
    price_usd: Decimal
    price_local: Decimal
    price_without_tax_usd: Decimal
    price_without_tax_local: Decimal
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SalesIvaDTO:
    """Sales-VAT rate plus the note written at the resolution site."""

    rate: Decimal
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeliveryPromiseDTO:
    """Promise tier plus the notes written at each promise branch."""

    tier: int
    quotation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalculationInputDTO:
    """Complete input for a country calculator.

    Attributes:
        amazon_price_usd: Selected Amazon amount including resolved shipping.
        policy: Common flow result for weight, tariff, margin and courier.
        exchange_rate: Country exchange rate resolved by the Strategy.
        settings: Country constants loaded from ``oc_setting``.
    """

    amazon_price_usd: Decimal
    policy: ResolvedPolicyDTO
    exchange_rate: Decimal
    settings: CountrySettingsDTO


def decimal_from_row(value: Any, field_name: str) -> Decimal:
    """Convert a database value to Decimal.

    Args:
        value: Value returned by the MySQL driver.
        field_name: Column name used in an actionable error.

    Returns:
        Decimal: Exact finite numeric value.
    """
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Database field {field_name} must be numeric.") from exc
    if not parsed.is_finite():
        raise ValueError(f"Database field {field_name} must be finite.")
    return parsed
