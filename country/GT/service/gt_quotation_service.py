"""Guatemala calculator and Amazon delivery-promise policy."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    SelectedOfferDTO,
)
from Utils.exceptions import ProductQuotationError
from Utils.price_rounding import round_price

GT_SETTING_KEYS = (
    "tipo_de_cambio",
    "currency_code",
    "margen",
    "tarifa_de_flete",
    "tarifa_de_flete_courier",
    "desaduanaje",
    "courier_desaduanaje",
    "seguro_valor_producto",
    "danger_dolar",
    "dias_importacion_amz",
    "global_store_promises",
    "kilos_por_libra",
    "default_arancel",
    "default_restriction",
    "default_arancel_category_cod",
    "default_danger_good_active",
    "default_courier",
    "default_iva_venta",
    "iva_importacion",
    "special_discount_threshold",
    "max_product_weight_kg",
    "price_rounding_step",
    "allowed_offer_price_types",
    "unavailable_offer_terms",
    "preorder_offer_terms",
    "promise_oos_terms",
    "promise_in_stock_exact_terms",
    "promise_in_stock_contains_terms",
    "restricted_guidance_term",
    "amazon_new_condition",
    "amazon_fulfillment_type",
    "amazon_fulfillment_free_shipping",
    "prefer_offer_with_delivery_range",
    "quotation_timezone",
    "promise_preorder_tier",
    "promise_missing_delivery_tier",
    "promise_below_range_tier",
    "promise_fallback_tier",
)


class GuatemalaQuotationService:
    """Contain only the GT calculations selected through Strategy."""

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Calculate GT landed cost USD and final local sale price.

        Args:
            calculation: Amazon price, resolved common policy, exchange rate
                and GT constants.

        Returns:
            CalculationResultDTO: Landed cost USD, sale price USD and rounded
                GTQ price.
        """
        policy = calculation.policy
        settings = calculation.settings
        amazon_price_usd = calculation.amazon_price_usd
        kilos_per_pound = settings.decimal("kilos_por_libra")
        if kilos_per_pound <= 0:
            raise ProductQuotationError("kilos_por_libra must be greater than zero.")

        # La tarifa está expresada por libra, así que el peso se convierte y se
        # redondea hacia arriba antes de multiplicar.
        weight_lb = (policy.weight_kg / kilos_per_pound).to_integral_value(
            rounding=ROUND_CEILING
        )

        # El modo de importación solo cambia qué key se lee; el resto del
        # cálculo es idéntico para courier y no courier.
        freight_rate = settings.decimal(
            "tarifa_de_flete_courier" if policy.courier else "tarifa_de_flete"
        )
        customs_clearance = settings.decimal(
            "courier_desaduanaje" if policy.courier else "desaduanaje"
        )
        freight_usd = weight_lb * freight_rate
        insurance_usd = amazon_price_usd * settings.decimal("seguro_valor_producto")

        # Esta suma se guarda porque los dos pasos siguientes la reutilizan.
        tariff_base = amazon_price_usd + freight_usd + insurance_usd
        tariff_usd = tariff_base * policy.arancel_percentage

        # Este componente solo entra cuando courier es true; si no, queda en 0.
        import_vat = (
            (tariff_base + tariff_usd) * settings.decimal("iva_importacion")
            if policy.courier
            else Decimal("0")
        )
        danger_usd = (
            settings.decimal("danger_dolar")
            if policy.danger_good_active
            else Decimal("0")
        )
        cost_usd = (
            amazon_price_usd
            + customs_clearance
            + tariff_usd
            + import_vat
            + freight_usd
            + danger_usd
        )

        price_without_vat_usd = cost_usd * policy.margin_percentage
        # Courier arma esta base sumando componentes; no courier reutiliza
        # el precio con margen que ya se calculó arriba.
        if policy.courier:
            margin_markup = max(policy.margin_percentage - Decimal("1"), Decimal("0"))
            vat_base_usd = (
                freight_usd
                + cost_usd * margin_markup
                + customs_clearance
                + danger_usd
            )
        else:
            vat_base_usd = price_without_vat_usd
        price_usd = price_without_vat_usd + (
            vat_base_usd * settings.decimal("default_iva_venta")
        )
        return CalculationResultDTO(
            cost_usd=cost_usd,
            price_usd=price_usd,
            price_local=round_price(
                price_usd * calculation.exchange_rate,
                settings,
            ),
        )

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> int:
        """Map Amazon delivery data to a Guatemala promise tier.

        Args:
            offer: Selected offer and its maximum delivery date.
            courier: Resolved courier mode. GT currently does not shift tiers.
            settings: GT timezone, import buffer, terms and promise ranges.

        Returns:
            int: OpenCart Amazon promise tier.
        """
        # GT no usa el modo courier para la promesa; se recibe solo por
        # contrato y se descarta de forma explícita.
        del courier

        # Primero se resuelve preventa/OOS/disponibilidad desconocida. Solo
        # si no aplica se entra al conteo por fecha.
        if self._is_preorder_or_oos_availability(offer.availability, settings):
            return settings.integer("promise_preorder_tier")

        timezone_name = settings.require("quotation_timezone")
        target = self._parse_delivery_date(offer.delivery_range_max, timezone_name)
        if target is None:
            return settings.integer("promise_missing_delivery_tier")

        # El conteo va día por día desde hoy hasta la fecha ya convertida a
        # la zona del país, saltando sábados y domingos, y al total se le
        # suma el buffer de importación.
        today = datetime.now(ZoneInfo(timezone_name)).date()
        business_days = 0
        current = today
        while current < target:
            current += timedelta(days=1)
            if current.weekday() < 5:
                business_days += 1
        total_days = business_days + settings.integer("dias_importacion_amz")

        # Días por debajo del rango más bajo caen al tier corto. El resto se
        # recorre de menor a mayor; un tope ausente significa "desde este
        # mínimo en adelante". El tier de preventa no se asigna por días.
        ranges = self._promise_ranges(settings.require("global_store_promises"))
        preorder_tier = settings.integer("promise_preorder_tier")
        lowest_start = min(bounds[0] for bounds in ranges.values())
        if total_days < lowest_start:
            return settings.integer("promise_below_range_tier")
        for tier, (low, high) in sorted(ranges.items(), key=lambda item: item[1][0]):
            if tier == preorder_tier:
                continue
            if high is None and total_days >= low:
                return tier
            if high is not None and low <= total_days <= high:
                return tier
        return settings.integer("promise_fallback_tier")

    @staticmethod
    def _is_preorder_or_oos_availability(
        availability: str,
        settings: CountrySettingsDTO,
    ) -> bool:
        """Return whether availability maps to the preorder/OOS promise tier.

        Args:
            availability: Raw Amazon availability text.
            settings: Preorder, OOS and in-stock term lists.

        Returns:
            bool: True when the text is preventa, OOS or unknown non-empty.
        """
        availability_lower = availability.casefold()
        marker_lists = (
            settings.string_list("promise_oos_terms"),
            settings.string_list("preorder_offer_terms"),
        )
        if any(
            term.casefold() in availability_lower
            for terms in marker_lists
            for term in terms
        ):
            return True

        # Vacío o texto de stock conocido sigue al conteo por fecha. Cualquier
        # otro texto no vacío se trata como preventa/OOS.
        in_stock = (
            availability == ""
            or availability in settings.string_list("promise_in_stock_exact_terms")
            or any(
                term in availability
                for term in settings.string_list("promise_in_stock_contains_terms")
            )
        )
        return availability != "" and not in_stock

    @staticmethod
    def _parse_delivery_date(raw_value: str | None, timezone_name: str):
        """Parse Amazon max date and convert it to the country timezone.

        Args:
            raw_value: ISO-like ``deliveryRange.max``, or ``None``.
            timezone_name: Country timezone used for the calendar date.

        Returns:
            datetime.date | None: Local date, or ``None`` when unusable.
        """
        if not raw_value:
            return None
        text = raw_value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        return parsed.astimezone(ZoneInfo(timezone_name)).date()

    @staticmethod
    def _promise_ranges(raw_promises: str) -> dict[int, tuple[int, int | None]]:
        """Parse numeric ranges from the existing promise setting.

        Args:
            raw_promises: Serialized ``global_store_promises`` mapping.

        Returns:
            dict[int, tuple[int, int | None]]: Closed ranges as min/max, or
                an open-ended min when the text has no upper bound.
        """
        # El setting es un mapa serializado, así que se extraen rangos
        # "N-M" o un solo número en textos tipo "después de N".
        ranges: dict[int, tuple[int, int | None]] = {}
        matches = re.findall(
            r"['\"]?(\d+)['\"]?\s*:\s*['\"]([^'\"]+)",
            raw_promises,
        )
        for tier, text in matches:
            closed = re.search(r"(\d+)\s*-\s*(\d+)", text)
            if closed:
                ranges[int(tier)] = (int(closed.group(1)), int(closed.group(2)))
                continue
            open_ended = re.search(r"(?:despu[eé]s|after)\s+(?:de\s+)?(\d+)", text, re.I)
            if open_ended:
                ranges[int(tier)] = (int(open_ended.group(1)) + 1, None)
        if not ranges:
            raise ProductQuotationError(
                "global_store_promises must define at least one numeric tier range."
            )
        return ranges
