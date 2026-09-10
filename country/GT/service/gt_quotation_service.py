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
    DeliveryPromiseDTO,
    SelectedOfferDTO,
)
from Utils.exceptions import ProductQuotationError
from Utils.price_rounding import round_price


def _operation_notes(name: str, formula: str, values: str) -> tuple[str, str]:
    """Describe one operation as a variable formula, then with numbers."""
    return (f"{name} = {formula}", f"{name} = {values}")

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
        exchange_rate = calculation.exchange_rate
        notes: list[str] = []
        weight_lb = policy.weight_lb
        notes.extend(
            _operation_notes(
                "weight_lb",
                "policy.weight_lb",
                f"{weight_lb}",
            )
        )
        notes.extend(
            _operation_notes(
                "amazon_price_usd",
                "calculation.amazon_price_usd",
                f"{amazon_price_usd}",
            )
        )

        # El modo de importación solo cambia qué key se lee; el resto del
        # cálculo es idéntico para courier y no courier.
        if policy.courier:
            freight_key = "tarifa_de_flete_courier"
            clearance_key = "courier_desaduanaje"
            notes.append("Calculadora GT en modo courier.")
        else:
            freight_key = "tarifa_de_flete"
            clearance_key = "desaduanaje"
            notes.append("Calculadora GT en modo no courier.")
        freight_rate = settings.decimal(freight_key)
        customs_clearance = settings.decimal(clearance_key)
        notes.extend(
            _operation_notes(
                "freight_rate",
                f"settings.{freight_key}",
                f"{freight_rate}",
            )
        )
        notes.extend(
            _operation_notes(
                "customs_clearance",
                f"settings.{clearance_key}",
                f"{customs_clearance}",
            )
        )
        freight_usd = weight_lb * freight_rate
        notes.extend(
            _operation_notes(
                "freight_usd",
                "weight_lb * freight_rate",
                f"{weight_lb} * {freight_rate} = {freight_usd}",
            )
        )
        insurance_rate = settings.decimal("seguro_valor_producto")
        insurance_usd = amazon_price_usd * insurance_rate
        notes.extend(
            _operation_notes(
                "insurance_usd",
                "amazon_price_usd * insurance_rate",
                f"{amazon_price_usd} * {insurance_rate} = {insurance_usd}",
            )
        )

        # Esta suma se guarda porque los dos pasos siguientes la reutilizan.
        tariff_base = amazon_price_usd + freight_usd + insurance_usd
        notes.extend(
            _operation_notes(
                "tariff_base",
                "amazon_price_usd + freight_usd + insurance_usd",
                f"{amazon_price_usd} + {freight_usd} + {insurance_usd} = {tariff_base}",
            )
        )
        tariff_usd = tariff_base * policy.arancel_percentage
        notes.extend(
            _operation_notes(
                "tariff_usd",
                "tariff_base * arancel_percentage",
                f"{tariff_base} * {policy.arancel_percentage} = {tariff_usd}",
            )
        )

        # Este componente solo entra cuando courier es true; si no, queda en 0.
        if policy.courier:
            import_iva_rate = settings.decimal("iva_importacion")
            import_iva = (tariff_base + tariff_usd) * import_iva_rate
            notes.extend(
                _operation_notes(
                    "import_iva",
                    "(tariff_base + tariff_usd) * import_iva_rate",
                    f"({tariff_base} + {tariff_usd}) * {import_iva_rate} = {import_iva}",
                )
            )
        else:
            import_iva = Decimal("0")
            notes.extend(
                _operation_notes(
                    "import_iva",
                    "0 (non-courier)",
                    f"{import_iva}",
                )
            )
        if policy.danger_good_active:
            danger_usd = settings.decimal("danger_dolar")
            notes.extend(
                _operation_notes(
                    "danger_usd",
                    "settings.danger_dolar",
                    f"{danger_usd}",
                )
            )
        else:
            danger_usd = Decimal("0")
            notes.extend(
                _operation_notes(
                    "danger_usd",
                    "0 (danger_good_active is false)",
                    f"{danger_usd}",
                )
            )
        cost_usd = (
            amazon_price_usd
            + customs_clearance
            + tariff_usd
            + import_iva
            + freight_usd
            + danger_usd
            + insurance_usd
        )
        notes.extend(
            _operation_notes(
                "cost_usd",
                "amazon_price_usd + customs_clearance + tariff_usd + import_iva + freight_usd + danger_usd + insurance_usd",
                f"{amazon_price_usd} + {customs_clearance} + {tariff_usd} + {import_iva} + {freight_usd} + {danger_usd} + {insurance_usd} = {cost_usd}",
            )
        )

        price_without_iva_usd = cost_usd * policy.margin_percentage
        notes.extend(
            _operation_notes(
                "price_without_iva_usd",
                "cost_usd * margin_percentage",
                f"{cost_usd} * {policy.margin_percentage} = {price_without_iva_usd}",
            )
        )
        # Courier arma esta base sumando componentes; no courier reutiliza
        # el precio con margen que ya se calculó arriba.
        if policy.courier:
            margin_markup = max(policy.margin_percentage - Decimal("1"), Decimal("0"))
            notes.extend(
                _operation_notes(
                    "margin_markup",
                    "max(margin_percentage - 1, 0)",
                    f"max({policy.margin_percentage} - 1, 0) = {margin_markup}",
                )
            )
            iva_base_usd = (
                freight_usd
                + cost_usd * margin_markup
                + customs_clearance
                + danger_usd
            )
            notes.extend(
                _operation_notes(
                    "iva_base_usd",
                    "freight_usd + cost_usd * margin_markup + customs_clearance + danger_usd",
                    f"{freight_usd} + {cost_usd} * {margin_markup} + {customs_clearance} + {danger_usd} = {iva_base_usd}",
                )
            )
        else:
            iva_base_usd = price_without_iva_usd
            notes.extend(
                _operation_notes(
                    "iva_base_usd",
                    "price_without_iva_usd",
                    f"{iva_base_usd}",
                )
            )
        sales_iva_rate = settings.decimal("default_iva_venta")
        sales_iva_usd = iva_base_usd * sales_iva_rate
        notes.extend(
            _operation_notes(
                "sales_iva_usd",
                "iva_base_usd * sales_iva_rate",
                f"{iva_base_usd} * {sales_iva_rate} = {sales_iva_usd}",
            )
        )
        price_usd = price_without_iva_usd + sales_iva_usd
        notes.extend(
            _operation_notes(
                "price_usd",
                "price_without_iva_usd + sales_iva_usd",
                f"{price_without_iva_usd} + {sales_iva_usd} = {price_usd}",
            )
        )
        price_local_raw = price_usd * exchange_rate
        notes.extend(
            _operation_notes(
                "price_local_raw",
                "price_usd * exchange_rate",
                f"{price_usd} * {exchange_rate} = {price_local_raw}",
            )
        )
        price_local = round_price(price_local_raw, settings)
        notes.extend(
            _operation_notes(
                "price_local",
                "round_price(price_local_raw)",
                f"round_price({price_local_raw}) = {price_local}",
            )
        )
        price_without_tax_local = price_without_iva_usd * exchange_rate
        notes.extend(
            _operation_notes(
                "price_without_tax_local",
                "price_without_iva_usd * exchange_rate",
                f"{price_without_iva_usd} * {exchange_rate} = {price_without_tax_local}",
            )
        )
        return CalculationResultDTO(
            cost_usd=cost_usd,
            price_usd=price_usd,
            price_local=price_local,
            price_without_tax_usd=price_without_iva_usd,
            price_without_tax_local=price_without_tax_local,
            quotation_notes=tuple(notes),
        )

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> DeliveryPromiseDTO:
        """Map Amazon delivery data to a Guatemala promise tier.

        Args:
            offer: Selected offer and its maximum delivery date.
            courier: Resolved courier mode. GT currently does not shift tiers.
            settings: GT timezone, import buffer, terms and promise ranges.

        Returns:
            DeliveryPromiseDTO: OpenCart Amazon promise tier and notes.
        """
        # GT no usa el modo courier para la promesa; se recibe solo por
        # contrato y se descarta de forma explícita.
        del courier

        # Primero se resuelve preventa/OOS/disponibilidad desconocida. Solo
        # si no aplica se entra al conteo por fecha.
        if self._is_preorder_or_oos_availability(offer.availability, settings):
            tier = settings.integer("promise_preorder_tier")
            return DeliveryPromiseDTO(
                tier=tier,
                quotation_notes=(
                    f"Promesa GT: preventa/OOS/disponibilidad desconocida; "
                    f"tier {tier} (promise_preorder_tier).",
                ),
            )

        timezone_name = settings.require("quotation_timezone")
        target = self._parse_delivery_date(offer.delivery_range_max, timezone_name)
        if target is None:
            tier = settings.integer("promise_missing_delivery_tier")
            return DeliveryPromiseDTO(
                tier=tier,
                quotation_notes=(
                    f"Promesa GT: sin fecha de entrega usable; "
                    f"tier {tier} (promise_missing_delivery_tier).",
                ),
            )

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
        import_days = settings.integer("dias_importacion_amz")
        total_days = business_days + import_days

        # Días por debajo del rango más bajo caen al tier corto. El resto se
        # recorre de menor a mayor; un tope ausente significa "desde este
        # mínimo en adelante". El tier de preventa no se asigna por días.
        ranges = self._promise_ranges(settings.require("global_store_promises"))
        preorder_tier = settings.integer("promise_preorder_tier")
        lowest_start = min(bounds[0] for bounds in ranges.values())
        if total_days < lowest_start:
            tier = settings.integer("promise_below_range_tier")
            return DeliveryPromiseDTO(
                tier=tier,
                quotation_notes=(
                    f"Promesa GT: {total_days} días (hábiles + "
                    f"dias_importacion_amz={import_days}) bajo el rango mínimo; "
                    f"tier {tier} (promise_below_range_tier).",
                ),
            )
        for tier, (low, high) in sorted(ranges.items(), key=lambda item: item[1][0]):
            if tier == preorder_tier:
                continue
            if high is None and total_days >= low:
                return DeliveryPromiseDTO(
                    tier=tier,
                    quotation_notes=(
                        f"Promesa GT: {total_days} días caen en rango abierto "
                        f"desde {low}; tier {tier} (global_store_promises).",
                    ),
                )
            if high is not None and low <= total_days <= high:
                return DeliveryPromiseDTO(
                    tier=tier,
                    quotation_notes=(
                        f"Promesa GT: {total_days} días en rango {low}-{high}; "
                        f"tier {tier} (global_store_promises).",
                    ),
                )
        tier = settings.integer("promise_fallback_tier")
        return DeliveryPromiseDTO(
            tier=tier,
            quotation_notes=(
                f"Promesa GT: {total_days} días fuera de rangos; "
                f"tier {tier} (promise_fallback_tier).",
            ),
        )

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
