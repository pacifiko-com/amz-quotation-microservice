"""Costa Rica calculator and Amazon delivery-promise policy."""

from __future__ import annotations

import re
from decimal import ROUND_CEILING, Decimal

from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    SelectedOfferDTO,
)
from Utils.price_rounding import round_price

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
        # El modo solo elige el prefijo de las keys; el resto del cálculo
        # sigue el mismo orden.
        mode = "courier" if policy.courier else "poliza"

        # Las tarifas están expresadas por kilo, así que el peso se redondea
        # hacia arriba antes de multiplicar.
        weight_kg = policy.weight_kg.to_integral_value(rounding=ROUND_CEILING)

        # Este componente es opcional y se apaga con un flag de configuración.
        usa_tax = (
            amazon_price_usd * settings.decimal("tax_usa_rate")
            if settings.boolean("tax_usa")
            else Decimal("0")
        )

        # Este subtotal se calcula primero porque los tres pasos siguientes
        # lo reutilizan. Usa keys distintas a las del flete que va más abajo.
        customs_insurance = amazon_price_usd * settings.decimal(
            f"{mode}_seguro_aduanas"
        )
        customs_freight = weight_kg * settings.decimal(f"{mode}_flete_aduana_kg")
        cif = amazon_price_usd + customs_insurance + customs_freight

        # El orden importa: el segundo componente recibe el subtotal ya
        # incrementado por el primero.
        tariff = cif * policy.arancel_percentage
        customs_vat = (cif + tariff) * settings.decimal(f"{mode}_iva_aduanas")
        law_6946 = cif * settings.decimal("ley_6946")

        # Estos componentes no reutilizan el subtotal anterior.
        real_freight = weight_kg * settings.decimal(f"{mode}_flete_kg")
        fuel_fee = real_freight * settings.decimal(f"{mode}_fee_combustible")
        clearance = settings.decimal(f"{mode}_desaduanaje")
        freight_insurance = amazon_price_usd * settings.decimal(
            f"{mode}_seguro_flete"
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
        # Este cargo se suma después del margen, no antes: entra al costo y al
        # precio como monto fijo, pero no forma parte de la base multiplicada.
        permit_fee = (
            settings.decimal("courier_tramite_permisos")
            if policy.courier
            else Decimal("0")
        )
        cost_usd = base_cost + permit_fee
        price_without_iva_usd = base_cost * policy.margin_percentage + permit_fee
        price_usd = price_without_iva_usd * (Decimal("1") + policy.sales_iva_rate)

        # CR aplica el recargo sobre el total y convierte al final; GT
        # convierte el precio USD ya calculado.
        return CalculationResultDTO(
            cost_usd=cost_usd,
            price_usd=price_usd,
            price_local=round_price(
                price_usd * calculation.exchange_rate,
                settings,
            ),
            price_without_tax_usd=price_without_iva_usd,
            price_without_tax_local=price_without_iva_usd * calculation.exchange_rate,
        )

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> int:
        """Map Amazon delivery text and courier mode to a CR promise tier.

        Args:
            offer: Selected Amazon offer and delivery information.
            courier: Resolved courier mode used to shift the promise.
            settings: CR terms, thresholds and shift.

        Returns:
            int: OpenCart Amazon promise tier.
        """
        # La preventa se resuelve antes que nada porque no depende del texto de
        # entrega que se parsea más abajo.
        availability = offer.availability.casefold()
        if any(
            term.casefold() in availability
            for term in settings.string_list("preorder_offer_terms")
        ):
            return settings.integer("promise_preorder_tier")

        # CR no recibe fecha estructurada sino el texto de entrega de Amazon,
        # así que los días se extraen del texto. "mañana" no trae número y se
        # traduce al equivalente configurado.
        text = offer.delivery_information.casefold()
        tomorrow = any(
            term.casefold() in text
            for term in settings.string_list("promise_tomorrow_terms")
        )
        days_match = re.search(r"\d+", text)
        days = (
            settings.integer("promise_tomorrow_days")
            if tomorrow
            else (int(days_match.group()) if days_match else None)
        )

        # El tipo de fulfillment solo selecciona el juego de umbrales a usar;
        # la comparación en cascada que sigue es la misma para ambos.
        is_af = offer.fulfillment_type == settings.require("amazon_fulfillment_type")
        prefix = "af" if is_af else "mf"
        if days is None:
            tier = settings.integer(f"promise_missing_delivery_{prefix}_tier")
        elif days < settings.integer(f"promise_{prefix}_tier_1_max_days"):
            tier = settings.integer("promise_tier_1_value")
        elif days < settings.integer(f"promise_{prefix}_tier_2_max_days"):
            tier = settings.integer("promise_tier_2_value")
        elif days < settings.integer(f"promise_{prefix}_tier_3_max_days"):
            tier = settings.integer("promise_tier_3_value")
        else:
            tier = settings.integer("promise_fallback_tier")
        # El modo courier desplaza el tier ya resuelto, con tope en el tier más
        # lento para que el desplazamiento no genere un valor inexistente.
        if courier:
            tier = min(
                tier + settings.integer("courier_promise_shift"),
                settings.integer("promise_fallback_tier"),
            )
        return tier
