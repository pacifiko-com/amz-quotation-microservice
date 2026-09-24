"""Tests for post-selection restriction and special-price resolution."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import Mock

from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    ResolvedPolicyDTO,
    SelectedOfferDTO,
)
from DTO.quotation_request_dto import PriceQuotationProductDTO, ProductQuotationDTO
from DTO.quotation_response_dto import ResultObjectDTO
from service.offer_selector import (
    resolve_courier_max_quantity,
    resolve_quotation_quantity,
)
from service.price_quotation_service import PriceQuotationService
from service.quotation_service import QuotationService


class _LinearCalculator:
    """Strategy stub that multiplies the Amazon USD amount by a constant."""

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Return local price as ten times the USD input."""
        return CalculationResultDTO(
            cost_usd=calculation.amazon_price_usd,
            price_usd=calculation.amazon_price_usd * Decimal("10"),
            price_local=calculation.amazon_price_usd * Decimal("10"),
            price_without_tax_usd=calculation.amazon_price_usd * Decimal("9"),
            price_without_tax_local=calculation.amazon_price_usd * Decimal("9"),
        )


class QuotationPriceTests(unittest.TestCase):
    """Lock the shared special and restriction decisions."""

    def setUp(self) -> None:
        """Build reusable settings, policy and offer fixtures."""
        self.settings = CountrySettingsDTO(
            {
                "restricted_guidance_term": "restricted",
                "special_discount_threshold": "0.05",
            }
        )
        self.policy = ResolvedPolicyDTO(
            weight_lb=Decimal("1"),
            arancel_percentage=Decimal("0.1"),
            margin_percentage=Decimal("1.2"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
            sales_iva_rate=Decimal("0.12"),
        )

    def test_resolve_quantity_uses_default_without_offer_limit(self) -> None:
        """Missing maxQuantity keeps quantity_default_tm."""
        settings = CountrySettingsDTO({"quantity_default_tm": "5"})
        offer = self._offer(price=Decimal("100"), list_price=None)

        quantity, note = resolve_quotation_quantity(settings, offer)

        self.assertEqual(quantity, 5)
        self.assertEqual(
            note,
            "Cantidad 5 desde quantity_default_tm; oferta sin maxQuantity.",
        )

    def test_resolve_quantity_caps_by_offer_max(self) -> None:
        """Default quantity is capped by the selected offer limit."""
        settings = CountrySettingsDTO({"quantity_default_tm": "5"})
        offer = self._offer(price=Decimal("100"), list_price=None, max_quantity=3)

        quantity, note = resolve_quotation_quantity(settings, offer)

        self.assertEqual(quantity, 3)
        self.assertEqual(
            note,
            "Cantidad 3 de offer.maxQuantity; Es menor a quantity_default_tm 5.",
        )

    def test_resolve_quantity_keeps_default_when_offer_max_is_higher(self) -> None:
        """A higher offer maxQuantity does not raise the default."""
        settings = CountrySettingsDTO({"quantity_default_tm": "5"})
        offer = self._offer(price=Decimal("100"), list_price=None, max_quantity=30)

        quantity, note = resolve_quotation_quantity(settings, offer)

        self.assertEqual(quantity, 5)
        self.assertEqual(
            note,
            "Cantidad 5 desde quantity_default_tm; offer.maxQuantity 30 no reduce el default.",
        )

    def test_resolve_courier_max_quantity_uses_setting_when_courier(self) -> None:
        """Courier products expose courier_quantity_max from oc_setting."""
        settings = CountrySettingsDTO({"courier_quantity_max": "12"})
        self.assertEqual(resolve_courier_max_quantity(settings, True), 12)

    def test_resolve_courier_max_quantity_is_null_when_not_courier(self) -> None:
        """Non-courier products do not expose a courier cap."""
        settings = CountrySettingsDTO({"courier_quantity_max": "12"})
        self.assertIsNone(resolve_courier_max_quantity(settings, False))

    def test_special_uses_local_prices_and_keeps_offer_cost(self) -> None:
        """A qualifying local discount publishes list as price and offer as special."""
        offer = self._offer(price=Decimal("100"), list_price=Decimal("120"))

        quoted = QuotationService._resolve_prices(
            _LinearCalculator(),
            offer,
            self.policy,
            Decimal("1"),
            self.settings,
        )

        self.assertEqual(quoted.amazon_price, Decimal("120"))
        self.assertEqual(quoted.special_amazon_price, Decimal("100"))
        self.assertEqual(quoted.cost_usd, Decimal("100"))
        self.assertEqual(quoted.price_usd, Decimal("1200"))
        self.assertEqual(quoted.price_local, Decimal("1200"))
        self.assertEqual(quoted.price_without_tax_usd, Decimal("1080"))
        self.assertEqual(quoted.price_without_tax_local, Decimal("1080"))
        self.assertEqual(quoted.special_price_usd, Decimal("1000"))
        self.assertEqual(quoted.special_price_local, Decimal("1000"))
        self.assertEqual(quoted.special_price_without_tax_usd, Decimal("900"))
        self.assertEqual(quoted.special_price_without_tax_local, Decimal("900"))

    def test_special_collapses_when_local_discount_is_below_threshold(self) -> None:
        """A local discount under the rounded percent threshold drops special."""
        offer = self._offer(price=Decimal("100"), list_price=Decimal("103"))

        quoted = QuotationService._resolve_prices(
            _LinearCalculator(),
            offer,
            self.policy,
            Decimal("1"),
            self.settings,
        )

        self.assertEqual(quoted.amazon_price, Decimal("100"))
        self.assertIsNone(quoted.special_amazon_price)
        self.assertEqual(quoted.cost_usd, Decimal("100"))
        self.assertEqual(quoted.price_usd, Decimal("1000"))
        self.assertEqual(quoted.price_local, Decimal("1000"))
        self.assertEqual(quoted.price_without_tax_usd, Decimal("900"))
        self.assertEqual(quoted.price_without_tax_local, Decimal("900"))
        self.assertIsNone(quoted.special_price_usd)
        self.assertIsNone(quoted.special_price_local)
        self.assertIsNone(quoted.special_price_without_tax_usd)
        self.assertIsNone(quoted.special_price_without_tax_local)

    def test_contains_restricted_guidance_aborts_selected_offer(self) -> None:
        """The selected offer aborts the product; no replacement is searched."""
        offer = self._offer(
            price=Decimal("100"),
            list_price=None,
            guidance="Export restricted item",
        )
        selector = Mock()
        selector.select.return_value = offer
        product = ProductQuotationDTO(
            product_id=10,
            amz_weight_kg=Decimal("1"),
            unspsc="123",
            amz_offers=({"offerId": "kept"},),
        )
        strategy = Mock()
        strategy.get_product_data.return_value = Mock(
            weight_lb=Decimal("1"),
            courier=False,
            partida=None,
            cabys=None,
        )
        strategy.get_unspsc_data.return_value = Mock(
            arancel_percentage=Decimal("0.1"),
            restriction=0,
            margin_percentage=Decimal("1.2"),
            danger_good_active=False,
            courier=False,
        )
        strategy.resolve_tariff_data.return_value = None
        strategy.get_category_tree_courier.return_value = False
        strategy.resolve_sales_iva_rate.return_value = Decimal("0.12")

        result = QuotationService(offer_selector=selector)._quote_product(
            strategy,
            product,
            CountrySettingsDTO(
                {
                    "margen": "1.2",
                    "max_product_weight_kg": "100",
                    "restricted_guidance_term": "restricted",
                }
            ),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Amazon offer is restricted.")
        self.assertEqual(result.offer_id, "offer-1")
        self.assertTrue(result.quotation_notes)
        self.assertTrue(
            any("restringida" in note for note in result.quotation_notes)
        )
        strategy.calculate.assert_not_called()

    def test_direct_price_quotes_without_offer_selection(self) -> None:
        """A known Amazon USD price is calculated without offers or promise."""
        product = PriceQuotationProductDTO(
            product_id=10,
            amz_weight_kg=Decimal("1"),
            unspsc="123",
            amazon_price_usd=Decimal("100"),
        )
        strategy = Mock()
        strategy.get_product_data.return_value = Mock(
            weight_lb=Decimal("1"),
            courier=False,
            partida=None,
            cabys=None,
        )
        strategy.get_unspsc_data.return_value = Mock(
            arancel_percentage=Decimal("0.1"),
            restriction=0,
            margin_percentage=Decimal("1.2"),
            danger_good_active=False,
            courier=False,
        )
        strategy.resolve_tariff_data.return_value = None
        strategy.get_category_tree_courier.return_value = False
        strategy.resolve_sales_iva_rate.return_value = Decimal("0.12")
        strategy.resolve_exchange_rate.return_value = Decimal("7.75")
        strategy.calculate.return_value = CalculationResultDTO(
            cost_usd=Decimal("80"),
            price_usd=Decimal("129.03"),
            price_local=Decimal("1000"),
            price_without_tax_usd=Decimal("115.21"),
            price_without_tax_local=Decimal("890"),
        )

        result = PriceQuotationService()._quote_product(
            strategy,
            product,
            CountrySettingsDTO(
                {
                    "margen": "1.2",
                    "max_product_weight_kg": "100",
                    "currency_code": "GTQ",
                }
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.amazon_price, Decimal("100"))
        self.assertIsNone(result.special_amazon_price)
        self.assertEqual(result.price_dolar, Decimal("129.03"))
        self.assertEqual(result.price_local, Decimal("1000"))
        self.assertEqual(result.price_without_tax_dolar, Decimal("115.21"))
        self.assertEqual(result.price_without_tax_local, Decimal("890"))
        self.assertEqual(result.cost_dolar, Decimal("80"))
        self.assertEqual(result.cost_local, Decimal("620"))
        self.assertIsNone(result.special_price_dolar)
        self.assertIsNone(result.special_price_local)
        self.assertIsNone(result.special_price_without_tax_dolar)
        self.assertIsNone(result.special_price_without_tax_local)
        self.assertEqual(result.offer_id, "")
        self.assertIsNone(result.delivery_promise_amz)
        self.assertIsNone(result.quantity)
        self.assertIsNone(result.max_quantity)
        strategy.resolve_delivery_promise.assert_not_called()
        strategy.calculate.assert_called_once()

    def test_product_result_serializes_special_fields_in_contract_order(self) -> None:
        """Public product keys follow the quotation response contract."""
        payload = ResultObjectDTO(
            success=True,
            message="ok",
            product_id=10,
            offer_id="offer-1",
            amazon_price=Decimal("120"),
            special_amazon_price=Decimal("100"),
            price_dolar=Decimal("120"),
            price_local=Decimal("1200"),
            price_without_tax_dolar=Decimal("107"),
            price_without_tax_local=Decimal("1070"),
            special_price_dolar=Decimal("100"),
            special_price_local=Decimal("1000"),
            special_price_without_tax_dolar=Decimal("89"),
            special_price_without_tax_local=Decimal("890"),
            cost_dolar=Decimal("80"),
            cost_local=Decimal("620"),
            exchange_rate=Decimal("7.75"),
            currency_code="GTQ",
            delivery_promise_amz=1,
            courier=False,
            restriction=0,
            partida="0012",
            cabys="1234567890123",
            quantity=3,
            max_quantity=12,
        ).to_dict()

        self.assertEqual(
            list(payload.keys()),
            [
                "product_id",
                "offer_id",
                "amazon_price",
                "special_amazon_price",
                "price_dolar",
                "price_local",
                "price_without_tax_dolar",
                "price_without_tax_local",
                "special_price_dolar",
                "special_price_local",
                "special_price_without_tax_dolar",
                "special_price_without_tax_local",
                "cost_dolar",
                "cost_local",
                "exchange_rate",
                "currency_code",
                "delivery_promise_amz",
                "courier",
                "restriction",
                "partida",
                "cabys",
                "quantity",
                "max_quantity",
                "quotation_notes",
                "success",
                "Message",
            ],
        )
        self.assertIsNone(
            ResultObjectDTO(
                success=True,
                message="ok",
                product_id=10,
            ).to_dict()["quantity"]
        )
        self.assertIsNone(
            ResultObjectDTO(
                success=True,
                message="ok",
                product_id=10,
            ).to_dict()["max_quantity"]
        )
        self.assertIsNone(
            ResultObjectDTO(
                success=False,
                message="fail",
                product_id=10,
            ).to_dict()["special_amazon_price"]
        )

    @staticmethod
    def _offer(
        price: Decimal,
        list_price: Decimal | None,
        guidance: str = "",
        max_quantity: int | None = None,
    ) -> SelectedOfferDTO:
        """Build a normalized offer for price-resolution tests."""
        return SelectedOfferDTO(
            offer_id="offer-1",
            price_usd=price,
            list_price_usd=list_price,
            shipping_usd=Decimal("0"),
            fulfillment_type="AMAZON_FULFILLMENT",
            availability="In Stock.",
            delivery_information="",
            delivery_range_max=None,
            buying_guidance=guidance,
            max_quantity=max_quantity,
        )


if __name__ == "__main__":
    unittest.main()
