"""Costa Rica calculator and Amazon delivery-promise policy."""

from __future__ import annotations

import re
from decimal import ROUND_CEILING, Decimal

from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    DeliveryPromiseDTO,
    SelectedOfferDTO,
)
from Utils.price_rounding import round_price


def _operation_notes(name: str, formula: str, values: str) -> tuple[str, str]:
    """Describe one operation as a variable formula, then with numbers."""
    return (f"{name} = {formula}", f"{name} = {values}")

CR_SETTING_KEYS = (
    "tipo_de_cambio",
    "currency_code",
    "margen",
    "default_arancel",
    "default_iva_venta",
    "tax_usa",
    "tax_usa_rate",
    "ley_6946",
    "courier_iva_aduanas",
    "poliza_iva_aduanas",
    "courier_flete_aduana_kg",
    "poliza_flete_aduana_kg",
    "courier_flete_kg",
    "poliza_flete_kg",
    "courier_fee_combustible",
    "poliza_fee_combustible",
    "courier_desaduanaje",
    "poliza_desaduanaje",
    "courier_seguro_aduanas",
    "poliza_seguro_aduanas",
    "courier_seguro_flete",
    "poliza_seguro_flete",
    "courier_tramite_permisos",
    "default_restriction",
    "default_arancel_category_cod",
    "default_danger_good_active",
    "default_courier",
    "special_discount_threshold",
    "max_product_weight_kg",
    "price_rounding_step",
    "allowed_offer_price_types",
    "unavailable_offer_terms",
    "preorder_offer_terms",
    "promise_tomorrow_terms",
    "promise_tomorrow_days",
    "restricted_guidance_term",
    "amazon_new_condition",
    "amazon_fulfillment_type",
    "amazon_fulfillment_free_shipping",
    "prefer_offer_with_delivery_range",
    "promise_af_tier_1_max_days",
    "promise_af_tier_2_max_days",
    "promise_af_tier_3_max_days",
    "promise_mf_tier_1_max_days",
    "promise_mf_tier_2_max_days",
    "promise_mf_tier_3_max_days",
    "promise_missing_delivery_af_tier",
    "promise_missing_delivery_mf_tier",
    "courier_promise_shift",
    "promise_preorder_tier",
    "promise_tier_1_value",
    "promise_tier_2_value",
    "promise_tier_3_value",
    "promise_fallback_tier",
)


class CostaRicaQuotationService:
    """Contain only the CR calculations selected through Strategy."""

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Calculate CR landed cost USD and final local sale price.

        Args:
            calculation: Amazon price, resolved common policy, exchange rate
                and CR constants.

        Returns:
            CalculationResultDTO: Landed cost USD, sale price USD and rounded
                CRC price.
        """
        policy = calculation.policy
        settings = calculation.settings
        amazon_price_usd = calculation.amazon_price_usd
        exchange_rate = calculation.exchange_rate
        notes: list[str] = []
        # El modo solo elige el prefijo de las keys; el resto del cálculo
        # sigue el mismo orden.
        if policy.courier:
            mode = "courier"
            notes.append("Calculadora CR en modo courier (keys courier_*).")
        else:
            mode = "poliza"
            notes.append("Calculadora CR en modo póliza (keys poliza_*).")

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

        # Este componente es opcional y se apaga con un flag de configuración.
        tax_usa_rate = settings.decimal("tax_usa_rate")
        if settings.boolean("tax_usa"):
            usa_tax = amazon_price_usd * tax_usa_rate
            notes.extend(
                _operation_notes(
                    "usa_tax",
                    "amazon_price_usd * tax_usa_rate",
                    f"{amazon_price_usd} * {tax_usa_rate} = {usa_tax}",
                )
            )
        else:
            usa_tax = Decimal("0")
            notes.extend(
                _operation_notes(
                    "usa_tax",
                    "0 (tax_usa is false)",
                    f"{usa_tax}",
                )
            )

        # Este subtotal se calcula primero porque los tres pasos siguientes
        # lo reutilizan. Usa keys distintas a las del flete que va más abajo.
        customs_insurance_rate = settings.decimal(f"{mode}_seguro_aduanas")
        customs_insurance = amazon_price_usd * customs_insurance_rate
        notes.extend(
            _operation_notes(
                "customs_insurance",
                f"amazon_price_usd * {mode}_seguro_aduanas",
                f"{amazon_price_usd} * {customs_insurance_rate} = {customs_insurance}",
            )
        )
        customs_freight_rate = settings.decimal(f"{mode}_flete_aduana_kg")
        customs_freight = weight_lb * customs_freight_rate
        notes.extend(
            _operation_notes(
                "customs_freight",
                f"weight_lb * {mode}_flete_aduana_kg",
                f"{weight_lb} * {customs_freight_rate} = {customs_freight}",
            )
        )
        cif = amazon_price_usd + customs_insurance + customs_freight
        notes.extend(
            _operation_notes(
                "cif",
                "amazon_price_usd + customs_insurance + customs_freight",
                f"{amazon_price_usd} + {customs_insurance} + {customs_freight} = {cif}",
            )
        )

        # El orden importa: el segundo componente recibe el subtotal ya
        # incrementado por el primero.
        tariff = cif * policy.arancel_percentage
        notes.extend(
            _operation_notes(
                "tariff",
                "cif * arancel_percentage",
                f"{cif} * {policy.arancel_percentage} = {tariff}",
            )
        )
        customs_vat_rate = settings.decimal(f"{mode}_iva_aduanas")
        customs_vat = (cif + tariff) * customs_vat_rate
        notes.extend(
            _operation_notes(
                "customs_vat",
                f"(cif + tariff) * {mode}_iva_aduanas",
                f"({cif} + {tariff}) * {customs_vat_rate} = {customs_vat}",
            )
        )
        law_6946_rate = settings.decimal("ley_6946")
        law_6946 = cif * law_6946_rate
        notes.extend(
            _operation_notes(
                "law_6946",
                "cif * ley_6946",
                f"{cif} * {law_6946_rate} = {law_6946}",
            )
        )

        # Estos componentes no reutilizan el subtotal anterior.
        real_freight_rate = settings.decimal(f"{mode}_flete_kg")
        real_freight = weight_lb * real_freight_rate
        notes.extend(
            _operation_notes(
                "real_freight",
                f"weight_lb * {mode}_flete_kg",
                f"{weight_lb} * {real_freight_rate} = {real_freight}",
            )
        )
        fuel_rate = settings.decimal(f"{mode}_fee_combustible")
        fuel_fee = real_freight * fuel_rate
        notes.extend(
            _operation_notes(
                "fuel_fee",
                f"real_freight * {mode}_fee_combustible",
                f"{real_freight} * {fuel_rate} = {fuel_fee}",
            )
        )
        clearance = settings.decimal(f"{mode}_desaduanaje")
        notes.extend(
            _operation_notes(
                "clearance",
                f"settings.{mode}_desaduanaje",
                f"{clearance}",
            )
        )
        freight_insurance_rate = settings.decimal(f"{mode}_seguro_flete")
        freight_insurance = amazon_price_usd * freight_insurance_rate
        notes.extend(
            _operation_notes(
                "freight_insurance",
                f"amazon_price_usd * {mode}_seguro_flete",
                f"{amazon_price_usd} * {freight_insurance_rate} = {freight_insurance}",
            )
        )
        base_cost = (
            amazon_price_usd
            + usa_tax
            + tariff
            + customs_vat
            + real_freight
            + fuel_fee
            + law_6946
            + clearance
            + freight_insurance
        )
        notes.extend(
            _operation_notes(
                "base_cost",
                "amazon_price_usd + usa_tax + tariff + customs_vat + real_freight + fuel_fee + law_6946 + clearance + freight_insurance",
                f"{amazon_price_usd} + {usa_tax} + {tariff} + {customs_vat} + {real_freight} + {fuel_fee} + {law_6946} + {clearance} + {freight_insurance} = {base_cost}",
            )
        )
        # Este cargo se suma después del margen, no antes: entra al costo y al
        # precio como monto fijo, pero no forma parte de la base multiplicada.
        if policy.courier:
            permit_fee = settings.decimal("courier_tramite_permisos")
            notes.extend(
                _operation_notes(
                    "permit_fee",
                    "settings.courier_tramite_permisos",
                    f"{permit_fee}",
                )
            )
        else:
            permit_fee = Decimal("0")
            notes.extend(
                _operation_notes(
                    "permit_fee",
                    "0 (non-courier)",
                    f"{permit_fee}",
                )
            )
        cost_usd = base_cost + permit_fee
        notes.extend(
            _operation_notes(
                "cost_usd",
                "base_cost + permit_fee",
                f"{base_cost} + {permit_fee} = {cost_usd}",
            )
        )
        price_without_iva_usd = base_cost * policy.margin_percentage + permit_fee
        notes.extend(
            _operation_notes(
                "price_without_iva_usd",
                "base_cost * margin_percentage + permit_fee",
                f"{base_cost} * {policy.margin_percentage} + {permit_fee} = {price_without_iva_usd}",
            )
        )
        price_usd = price_without_iva_usd * (Decimal("1") + policy.sales_iva_rate)
        notes.extend(
            _operation_notes(
                "price_usd",
                "price_without_iva_usd * (1 + sales_iva_rate)",
                f"{price_without_iva_usd} * (1 + {policy.sales_iva_rate}) = {price_usd}",
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

        # CR aplica el recargo sobre el total y convierte al final; GT
        # convierte el precio USD ya calculado.
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
        """Map Amazon delivery text and courier mode to a CR promise tier.

        Args:
            offer: Selected Amazon offer and delivery information.
            courier: Resolved courier mode used to shift the promise.
            settings: CR terms, thresholds and shift.

        Returns:
            DeliveryPromiseDTO: OpenCart Amazon promise tier and notes.
        """
        notes: list[str] = []
        # La preventa se resuelve antes que nada porque no depende del texto de
        # entrega que se parsea más abajo.
        availability = offer.availability.casefold()
        if any(
            term.casefold() in availability
            for term in settings.string_list("preorder_offer_terms")
        ):
            tier = settings.integer("promise_preorder_tier")
            notes.append(
                f"Promesa CR: preventa por availability; tier {tier} "
                "(promise_preorder_tier)."
            )
            return DeliveryPromiseDTO(tier=tier, quotation_notes=tuple(notes))

        # CR no recibe fecha estructurada sino el texto de entrega de Amazon,
        # así que los días se extraen del texto. "mañana" no trae número y se
        # traduce al equivalente configurado.
        text = offer.delivery_information.casefold()
        tomorrow = any(
            term.casefold() in text
            for term in settings.string_list("promise_tomorrow_terms")
        )
        days_match = re.search(r"\d+", text)
        if tomorrow:
            days = settings.integer("promise_tomorrow_days")
            notes.append(
                f"Promesa CR: texto de mañana; días={days} "
                "(promise_tomorrow_days)."
            )
        elif days_match:
            days = int(days_match.group())
            notes.append(f"Promesa CR: días extraídos del texto de entrega: {days}.")
        else:
            days = None
            notes.append("Promesa CR: el texto de entrega no trae días.")

        # El tipo de fulfillment solo selecciona el juego de umbrales a usar;
        # la comparación en cascada que sigue es la misma para ambos.
        is_af = offer.fulfillment_type == settings.require("amazon_fulfillment_type")
        if is_af:
            prefix = "af"
            notes.append("Promesa CR: umbrales Amazon fulfillment (af).")
        else:
            prefix = "mf"
            notes.append("Promesa CR: umbrales merchant fulfillment (mf).")
        if days is None:
            tier = settings.integer(f"promise_missing_delivery_{prefix}_tier")
            notes.append(
                f"Promesa CR: sin días; tier {tier} "
                f"(promise_missing_delivery_{prefix}_tier)."
            )
        elif days < settings.integer(f"promise_{prefix}_tier_1_max_days"):
            tier = settings.integer("promise_tier_1_value")
            notes.append(
                f"Promesa CR: {days} días bajo umbral 1; tier {tier}."
            )
        elif days < settings.integer(f"promise_{prefix}_tier_2_max_days"):
            tier = settings.integer("promise_tier_2_value")
            notes.append(
                f"Promesa CR: {days} días bajo umbral 2; tier {tier}."
            )
        elif days < settings.integer(f"promise_{prefix}_tier_3_max_days"):
            tier = settings.integer("promise_tier_3_value")
            notes.append(
                f"Promesa CR: {days} días bajo umbral 3; tier {tier}."
            )
        else:
            tier = settings.integer("promise_fallback_tier")
            notes.append(
                f"Promesa CR: {days} días sobre umbrales; "
                f"tier {tier} (promise_fallback_tier)."
            )
        # El modo courier desplaza el tier ya resuelto, con tope en el tier más
        # lento para que el desplazamiento no genere un valor inexistente.
        if courier:
            shifted = min(
                tier + settings.integer("courier_promise_shift"),
                settings.integer("promise_fallback_tier"),
            )
            notes.append(
                f"Promesa CR courier: tier {tier} desplazado a {shifted} "
                "(courier_promise_shift, tope promise_fallback_tier)."
            )
            tier = shifted
        return DeliveryPromiseDTO(tier=tier, quotation_notes=tuple(notes))
