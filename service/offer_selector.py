"""Canonical adapter and selector for variable Amazon offer payloads."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from DTO.quotation_context_dto import CountrySettingsDTO, SelectedOfferDTO
from Utils.exceptions import ProductQuotationError


class OfferSelector:
    """Select an eligible offer without calling Amazon."""

    def select(
        self,
        offers: tuple[dict[str, Any], ...],
        settings: CountrySettingsDTO,
        prefer_amazon_fulfillment: bool = False,
    ) -> SelectedOfferDTO:
        """Select and normalize one raw Amazon offer.

        Args:
            offers: Raw ``includedDataTypes.OFFERS`` objects in API order.
            settings: Eligibility, fulfillment and shipping policy.
            prefer_amazon_fulfillment: When true, insert the AF-first pass
                used by GT detail page and variants. When false, skip it as
                the GT cotizador does.

        Returns:
            SelectedOfferDTO: Canonical offer consumed by country calculators.

        Raises:
            ProductQuotationError: If no offer satisfies the configured rules.
        """
        selected: dict[str, Any] | None = None
        shipping: dict[str, Any] = {}
        selection_reason = ""
        notes: list[str] = []

        # la primera elegible que tenga deliveryRange.max.
        # La opción de envío es la primera de esa oferta que trae max, no
        # necesariamente shippingOptions[0].
        if settings.boolean("prefer_offer_with_delivery_range"):
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                if self._is_eligible(offer, settings) and self._has_delivery_range_max(
                    offer
                ):
                    selected = offer
                    shipping = self._first_shipping_with_max(offer)
                    selection_reason = "primera elegible con deliveryRange.max"
                    notes.append(
                        "Oferta seleccionada: primera elegible con deliveryRange.max."
                    )
                    break

        # Pasada intermedia, solo si el request la activa: primera elegible
        # con fulfillment de Amazon. El shipping es shippingOptions[0].
        if selected is None and prefer_amazon_fulfillment:
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                if self._is_eligible(offer, settings, require_amazon_fulfillment=True):
                    selected = offer
                    shipping = self._first_shipping_option(offer)
                    selection_reason = "primera elegible AMAZON_FULFILLMENT"
                    notes.append(
                        "Oferta seleccionada: primera elegible AMAZON_FULFILLMENT."
                    )
                    break

        # Última pasada: si nadie tenía max (ni AF cuando aplica), la primera
        # elegible en el orden recibido. El shipping es shippingOptions[0].
        if selected is None:
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                if self._is_eligible(offer, settings):
                    selected = offer
                    shipping = self._first_shipping_option(offer)
                    selection_reason = "primera oferta elegible"
                    notes.append("Oferta seleccionada: primera oferta elegible.")
                    break

        if selected is None:
            raise ProductQuotationError("No eligible Amazon offer was found.")

        fulfillment = str(selected.get("fulfillmentType") or "")
        shipping_usd = self._amount(
            _nested(shipping, "shippingCost", "value", "amount"),
            "shippingCost.value.amount",
            allow_missing=True,
        )
        # Para fulfillment de Amazon el shipping se fuerza a cero aunque la
        # oferta declare un costo.
        if (
            settings.boolean("amazon_fulfillment_free_shipping")
            and fulfillment == settings.require("amazon_fulfillment_type")
        ):
            shipping_usd = Decimal("0")
            notes.append(
                "Shipping forzado a 0 por amazon_fulfillment_free_shipping "
                "y fulfillment Amazon."
            )
        notes.append(f"Shipping USD {shipping_usd}; fulfillment {fulfillment or '(vacío)'}.")

        # El mismo shipping se suma al precio y al precio de lista, para que
        # ambas bases de cálculo queden en la misma unidad.
        price = self._amount(
            _nested(selected, "price", "value", "amount"),
            "price.value.amount",
        )
        list_price = self._optional_amount(
            _nested(selected, "listPrice", "value", "amount")
        )
        if list_price is not None:
            notes.append(f"List price Amazon {list_price} USD; se suma el shipping.")
        else:
            notes.append("Sin list price; un solo cálculo sobre la oferta.")
        offer_id = str(
            selected.get("offerId")
            or selected.get("offer_id")
            or selected.get("id")
            or ""
        )
        max_quantity = OfferSelector._optional_int(
            _nested(selected, "quantityLimits", "maxQuantity")
        )
        notes.append(f"Oferta {offer_id or '(sin id)'} seleccionada ({selection_reason}).")
        return SelectedOfferDTO(
            offer_id=offer_id,
            price_usd=price + shipping_usd,
            list_price_usd=(
                list_price + shipping_usd if list_price is not None else None
            ),
            shipping_usd=shipping_usd,
            fulfillment_type=fulfillment,
            availability=str(selected.get("availability") or ""),
            delivery_information=str(selected.get("deliveryInformation") or ""),
            delivery_range_max=self._delivery_range_max(shipping),
            buying_guidance=str(selected.get("buyingGuidance") or ""),
            buying_guidance_title=str(
                _nested(selected, "buyingGuidanceV2", "buyingGuidance", 0, "title") or ""
            ),
            buying_guidance_type=str(
                _nested(selected, "buyingGuidanceV2", "buyingGuidance", 0, "type") or ""
            ),
            max_quantity=max_quantity,
            selection_reason=selection_reason,
            quotation_notes=tuple(notes),
        )

    def _is_eligible(
        self,
        offer: dict[str, Any],
        settings: CountrySettingsDTO,
        require_amazon_fulfillment: bool = False,
    ) -> bool:
        """Evaluate configured offer eligibility.

        Args:
            offer: One raw Amazon offer.
            settings: Lists and literals stored in ``oc_setting``.

        Returns:
            bool: True when condition, price type, amount, exact restricted
                guidance and availability satisfy the selection filters.
        """
        # Si la oferta tiene la restricción de compra, no es elegible.
        restricted_term = settings.require("restricted_guidance_term").casefold()
        buying_guidance = str(offer.get("buyingGuidance") or "").casefold()
        if buying_guidance == restricted_term:
            return False

        # Si la oferta no es nueva, no es elegible.
        required_condition = settings.require("amazon_new_condition").casefold()
        product_condition = str(offer.get("productCondition") or "").casefold()
        condition_value = str(_nested(offer, "condition", "conditionValue") or "").casefold()
        if required_condition not in {product_condition, condition_value}:
            return False

        # Si el precio no es de tipo permitido, no es elegible.
        price_type = str(_nested(offer, "price", "priceType") or "").upper()
        allowed_types = {
            value.upper() for value in settings.string_list("allowed_offer_price_types")
        }
        if price_type not in allowed_types:
            return False

        # Si el precio es cero o negativo, no es elegible.
        price = self._optional_amount(_nested(offer, "price", "value", "amount"))
        if price is None or price <= 0:
            return False

        # Si la oferta no es de fulfillment de Amazon y se requiere, no es elegible.
        if (
            require_amazon_fulfillment
            and str(offer.get("fulfillmentType") or "")
            != settings.require("amazon_fulfillment_type")
        ):
            return False

        # Si la oferta no está disponible, no es elegible.
        availability = str(offer.get("availability") or "").casefold()
        return not any(
            term.casefold() in availability
            for term in settings.string_list("unavailable_offer_terms")
        )

    @staticmethod
    def _has_delivery_range_max(offer: dict[str, Any]) -> bool:
        """Whether any shipping option of the offer carries a max date.

        Args:
            offer: Raw Amazon offer.

        Returns:
            bool: True when at least one option has ``deliveryRange.max``.
        """
        return bool(OfferSelector._first_shipping_with_max(offer))

    @staticmethod
    def _first_shipping_with_max(offer: dict[str, Any]) -> dict[str, Any]:
        """Return the first shipping option that includes a max date.

        Args:
            offer: Raw Amazon offer.

        Returns:
            dict[str, Any]: Matching option, or an empty object.
        """
        for option in OfferSelector._shipping_options(offer):
            if _nested(option, "deliveryRange", "max"):
                return option
        return {}

    @staticmethod
    def _first_shipping_option(offer: dict[str, Any]) -> dict[str, Any]:
        """Return the first shipping option in payload order.

        Args:
            offer: Raw Amazon offer.

        Returns:
            dict[str, Any]: First option, or an empty object.
        """
        options = OfferSelector._shipping_options(offer)
        return options[0] if options else {}

    @staticmethod
    def _shipping_options(offer: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize shippingOptions to a list of dictionaries.

        Args:
            offer: Raw Amazon offer.

        Returns:
            list[dict[str, Any]]: Valid shipping option objects.
        """
        options = offer.get("shippingOptions")
        if not isinstance(options, list):
            return []
        return [option for option in options if isinstance(option, dict)]

    @staticmethod
    def _delivery_range_max(shipping: dict[str, Any]) -> str | None:
        """Extract the selected shipping maximum delivery date.

        Args:
            shipping: Raw shipping option.

        Returns:
            str | None: Amazon ISO-like date, when provided.
        """
        value = _nested(shipping, "deliveryRange", "max")
        return str(value) if value else None

    @staticmethod
    def _amount(value: Any, field_name: str, allow_missing: bool = False) -> Decimal:
        """Parse a finite Amazon monetary amount.

        Args:
            value: Raw amount.
            field_name: Path used in error messages.
            allow_missing: Whether an absent value represents zero.

        Returns:
            Decimal: Exact amount.
        """
        if value is None and allow_missing:
            return Decimal("0")
        parsed = OfferSelector._optional_amount(value)
        if parsed is None:
            raise ProductQuotationError(f"Amazon offer field {field_name} is invalid.")
        return parsed

    @staticmethod
    def _optional_amount(value: Any) -> Decimal | None:
        """Parse an optional finite amount.

        Args:
            value: Raw amount or ``None``.

        Returns:
            Decimal | None: Parsed amount when valid.
        """
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        """Parse an optional positive integer.

        Args:
            value: Raw integer or ``None``.

        Returns:
            int | None: Parsed integer when valid.
        """
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None


def resolve_quotation_quantity(
    settings: CountrySettingsDTO,
    offer: SelectedOfferDTO,
) -> tuple[int, str]:
    """
    Return purchasable quantity and a note describing the decision.

    Args:
        settings: Country settings.
        offer: Selected Amazon offer.

    Returns:
        tuple[int, str]: Purchasable quantity and a note describing the decision.
    """
    default_quantity = settings.integer("quantity_default_tm")
    if offer.max_quantity is None:
        return (
            default_quantity,
            f"Cantidad {default_quantity} desde quantity_default_tm; oferta sin maxQuantity.",
        )

    if offer.max_quantity < default_quantity:
        return (
            offer.max_quantity,
            f"Cantidad {offer.max_quantity} de offer.maxQuantity; Es menor a quantity_default_tm {default_quantity}."
        )

    return (
        default_quantity,
        f"Cantidad {default_quantity} desde quantity_default_tm; offer.maxQuantity {offer.max_quantity} no reduce el default.",
    )


def _nested(value: Any, *path: str | int) -> Any:
    """Read a nested dict/list path without raising.

    Args:
        value: Root object.
        *path: String dictionary keys or integer list indexes.

    Returns:
        Any: Located value, or ``None`` when the path does not exist.
    """
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
    return current
