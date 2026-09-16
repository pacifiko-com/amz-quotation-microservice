"""Golden calculator tests derived from active GT and CR cotizadores."""

from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from country.CR.service.cr_quotation_service import CostaRicaQuotationService
from country.GT.service.gt_quotation_service import GuatemalaQuotationService
from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CountrySettingsDTO,
    ResolvedPolicyDTO,
    SelectedOfferDTO,
)
from Utils.price_rounding import round_price


class CountryCalculatorTests(unittest.TestCase):
    """Lock country-specific landed-cost and rounding behavior."""

    def test_shared_rounding_ceils_to_step(self) -> None:
        """Every country uses ceiling to the configured step, with no offset."""
        settings = CountrySettingsDTO({"price_rounding_step": "5"})

        self.assertEqual(round_price(Decimal("1142.064"), settings), Decimal("1145"))
        self.assertEqual(
            round_price(Decimal("76924"), CountrySettingsDTO({"price_rounding_step": "10"})),
            Decimal("76930"),
        )

    def test_gt_parses_existing_promise_format(self) -> None:
        """The legacy single-quoted setting yields all numeric tiers."""
        raw = "{'1':'6-14 days','2':'15-20 days','3':'21-30 days'}"

        ranges = GuatemalaQuotationService._promise_ranges(raw)

        self.assertEqual(ranges[1], (6, 14))
        self.assertEqual(ranges[3], (21, 30))

    def test_gt_parses_open_ended_promise_range(self) -> None:
        """Open-ended texts become a minimum with no upper bound."""
        raw = "{'1':'6-12 days','5':'después de 25 días'}"

        ranges = GuatemalaQuotationService._promise_ranges(raw)

        self.assertEqual(ranges[1], (6, 12))
        self.assertEqual(ranges[5], (26, None))

    def test_gt_unknown_availability_uses_preorder_tier(self) -> None:
        """A non-empty unknown availability does not enter date counting."""
        settings = self._gt_promise_settings()
        offer = self._offer(availability="Temporarily delayed")

        result = GuatemalaQuotationService().resolve_delivery_promise(
            offer,
            False,
            settings,
        )

        self.assertEqual(result.tier, 4)

    def test_gt_in_stock_without_date_uses_missing_delivery_tier(self) -> None:
        """Known in-stock text without max date keeps the missing-date tier."""
        settings = self._gt_promise_settings()
        offer = self._offer(availability="In Stock.", delivery_range_max=None)

        result = GuatemalaQuotationService().resolve_delivery_promise(
            offer,
            False,
            settings,
        )

        self.assertEqual(result.tier, 3)

    def test_gt_envio_en_availability_is_treated_as_in_stock(self) -> None:
        """'Envío en' is a known in-stock marker, not unknown availability."""
        settings = self._gt_promise_settings()
        offer = self._offer(availability="Envío en 2 días", delivery_range_max=None)

        result = GuatemalaQuotationService().resolve_delivery_promise(
            offer,
            False,
            settings,
        )

        self.assertEqual(result.tier, 3)

    def test_gt_converts_delivery_date_to_country_timezone(self) -> None:
        """Timezone-aware Amazon dates are converted before taking the date."""
        local_date = GuatemalaQuotationService._parse_delivery_date(
            "2026-09-08T03:00:00Z",
            "America/Guatemala",
        )

        self.assertEqual(str(local_date), "2026-09-07")

    def test_gt_days_below_lowest_range_use_short_tier(self) -> None:
        """Totals under the first configured range use the short tier."""
        today = datetime.now(ZoneInfo("America/Guatemala")).date()
        settings = self._gt_promise_settings()
        offer = self._offer(
            "In Stock.",
            f"{today.isoformat()}T12:00:00-06:00",
        )

        result = GuatemalaQuotationService().resolve_delivery_promise(
            offer,
            False,
            settings,
        )

        self.assertEqual(result.tier, 1)

    def test_gt_below_range_ignores_preorder_tier_bounds(self) -> None:
        """Preorder ranges do not lower the assignable minimum."""
        today = datetime.now(ZoneInfo("America/Guatemala")).date()
        settings = CountrySettingsDTO(
            {
                **self._gt_promise_settings().values,
                "global_store_promises": (
                    "{'4':'1-5 days','1':'6-12 days','2':'13-18 days'}"
                ),
            }
        )
        offer = self._offer(
            "In Stock.",
            f"{today.isoformat()}T12:00:00-06:00",
        )

        result = GuatemalaQuotationService().resolve_delivery_promise(
            offer,
            False,
            settings,
        )

        self.assertEqual(result.tier, 1)

    def test_guatemala_non_courier_golden_case(self) -> None:
        """GT applies freight, insured tariff base, margin, VAT and ceil rounding."""
        settings = CountrySettingsDTO(
            {
                "kilos_por_libra": "0.453592",
                "tarifa_de_flete": "2",
                "tarifa_de_flete_courier": "3",
                "desaduanaje": "1",
                "courier_desaduanaje": "0",
                "poliza_flete_aduana_kg": "0",
                "courier_flete_aduana_kg": "0",
                "poliza_seguro_aduanas": "0",
                "courier_seguro_aduanas": "0",
                "poliza_seguro_flete": "0",
                "courier_seguro_flete": "0",
                "seguro_valor_producto": "0.01",
                "iva_importacion": "0.12",
                "danger_dolar": "5",
                "tipo_de_cambio": "7.5",
                "default_iva_venta": "0.12",
                "price_rounding_step": "5",
            }
        )
        policy = ResolvedPolicyDTO(
            weight_lb=Decimal("1"),
            arancel_percentage=Decimal("0.10"),
            margin_percentage=Decimal("1.20"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
            sales_iva_rate=Decimal("0.12"),
        )

        result = GuatemalaQuotationService().calculate(
            CalculationInputDTO(
                amazon_price_usd=Decimal("100"),
                policy=policy,
                exchange_rate=Decimal("7.5"),
                settings=settings,
            )
        )

        self.assertEqual(result.cost_usd, Decimal("113.00"))
        self.assertEqual(result.price_usd, Decimal("151.87200"))
        self.assertEqual(result.price_local, Decimal("1140"))
        self.assertEqual(result.price_without_tax_usd, Decimal("135.6000"))
        self.assertEqual(result.price_without_tax_local, Decimal("1017.00000"))

    def test_costa_rica_poliza_golden_case(self) -> None:
        """CR applies CIF, DAI, freight, Ley 6946, sales VAT and ceil-to-ten."""
        settings = CountrySettingsDTO(
            {
                "tax_usa": "0",
                "tax_usa_rate": "0.07",
                "poliza_seguro_aduanas": "0.005",
                "poliza_flete_aduana_kg": "2",
                "poliza_iva_aduanas": "0",
                "poliza_flete_kg": "2",
                "poliza_fee_combustible": "0",
                "ley_6946": "0.01",
                "poliza_desaduanaje": "10",
                "poliza_seguro_flete": "0.005",
                "courier_tramite_permisos": "14",
                "default_iva_venta": "0.13",
                "tipo_de_cambio": "500",
                "price_rounding_step": "10",
            }
        )
        policy = ResolvedPolicyDTO(
            weight_lb=Decimal("1"),
            arancel_percentage=Decimal("0.10"),
            margin_percentage=Decimal("1.10"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
            sales_iva_rate=Decimal("0.13"),
        )

        result = CostaRicaQuotationService().calculate(
            CalculationInputDTO(
                amazon_price_usd=Decimal("100"),
                policy=policy,
                exchange_rate=Decimal("500"),
                settings=settings,
            )
        )

        self.assertEqual(result.cost_usd, Decimal("123.77500"))
        self.assertEqual(result.price_usd, Decimal("153.852325"))
        self.assertEqual(result.price_local, Decimal("76930"))
        self.assertEqual(result.price_without_tax_usd, Decimal("136.152500"))
        self.assertEqual(result.price_without_tax_local, Decimal("68076.2500000"))

    def test_costa_rica_sales_iva_follows_cabys_rate(self) -> None:
        """CR multiplies the post-margin price by the resolved CABYS rate."""
        settings = CountrySettingsDTO(
            {
                "tax_usa": "0",
                "tax_usa_rate": "0.07",
                "poliza_seguro_aduanas": "0.005",
                "poliza_flete_aduana_kg": "2",
                "poliza_iva_aduanas": "0",
                "poliza_flete_kg": "2",
                "poliza_fee_combustible": "0",
                "ley_6946": "0.01",
                "poliza_desaduanaje": "10",
                "poliza_seguro_flete": "0.005",
                "courier_tramite_permisos": "14",
                "default_iva_venta": "0.13",
                "tipo_de_cambio": "500",
                "price_rounding_step": "10",
            }
        )
        base_policy = dict(
            weight_lb=Decimal("1"),
            arancel_percentage=Decimal("0.10"),
            margin_percentage=Decimal("1.10"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
        )
        exempt = CostaRicaQuotationService().calculate(
            CalculationInputDTO(
                amazon_price_usd=Decimal("100"),
                policy=ResolvedPolicyDTO(
                    **base_policy,
                    sales_iva_rate=Decimal("0"),
                    cabys="0000000000000",
                ),
                exchange_rate=Decimal("500"),
                settings=settings,
            )
        )
        reduced = CostaRicaQuotationService().calculate(
            CalculationInputDTO(
                amazon_price_usd=Decimal("100"),
                policy=ResolvedPolicyDTO(
                    **base_policy,
                    sales_iva_rate=Decimal("0.01"),
                    cabys="1111111111111",
                ),
                exchange_rate=Decimal("500"),
                settings=settings,
            )
        )

        self.assertEqual(exempt.cost_usd, Decimal("123.77500"))
        self.assertEqual(exempt.price_usd, Decimal("136.152500"))
        self.assertEqual(exempt.price_local, Decimal("68080"))
        self.assertEqual(exempt.price_without_tax_usd, Decimal("136.152500"))
        self.assertEqual(exempt.price_without_tax_local, Decimal("68076.2500000"))
        self.assertEqual(reduced.price_usd, Decimal("137.514025"))
        self.assertEqual(reduced.price_local, Decimal("68760"))
        self.assertEqual(reduced.price_without_tax_usd, Decimal("136.152500"))
        self.assertEqual(reduced.price_without_tax_local, Decimal("68076.2500000"))

    @staticmethod
    def _gt_promise_settings() -> CountrySettingsDTO:
        """Build the GT promise settings used by availability tests."""
        return CountrySettingsDTO(
            {
                "preorder_offer_terms": '["preventa","pre-order"]',
                "promise_oos_terms": '["out of stock","no disponible"]',
                "promise_in_stock_exact_terms": '["In Stock."]',
                "promise_in_stock_contains_terms": '["Envío en","Envio en"]',
                "promise_preorder_tier": "4",
                "promise_missing_delivery_tier": "3",
                "promise_below_range_tier": "1",
                "promise_fallback_tier": "5",
                "quotation_timezone": "America/Guatemala",
                "dias_importacion_amz": "5",
                "global_store_promises": "{'1':'6-12 days','2':'13-18 days','3':'19-25 days'}",
            }
        )

    @staticmethod
    def _offer(
        availability: str,
        delivery_range_max: str | None = "2026-09-10T00:00:00Z",
    ) -> SelectedOfferDTO:
        """Build a minimal offer for GT promise tests."""
        return SelectedOfferDTO(
            offer_id="offer-1",
            price_usd=Decimal("10"),
            list_price_usd=None,
            shipping_usd=Decimal("0"),
            fulfillment_type="AMAZON_FULFILLMENT",
            availability=availability,
            delivery_information="",
            delivery_range_max=delivery_range_max,
        )


if __name__ == "__main__":
    unittest.main()
