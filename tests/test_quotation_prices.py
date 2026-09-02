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
from service.price_quotation_service import PriceQuotationService
from service.quotation_service import QuotationService


class _LinearCalculator:
    """Strategy stub that multiplies the Amazon USD amount by a constant."""

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Return local price as ten times the USD input."""
        return CalculationResultDTO(
            cost_usd=calculation.amazon_price_usd,
            price_local=calculation.amazon_price_usd * Decimal("10"),
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
            weight_kg=Decimal("1"),
            arancel_percentage=Decimal("0.1"),
            margin_percentage=Decimal("1.2"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
        )

    def test_special_uses_local_prices_and_keeps_offer_cost(self) -> None:
        """A qualifying local discount publishes list as price and offer as special."""
        offer = self._offer(price=Decimal("100"), list_price=Decimal("120"))

        price, special, cost, local, special_local = QuotationService._resolve_prices(
            _LinearCalculator(),
            offer,
            self.policy,
            Decimal("1"),
            self.settings,
        )

        self.assertEqual(price, Decimal("120"))
        self.assertEqual(special, Decimal("100"))
        self.assertEqual(cost, Decimal("100"))
        self.assertEqual(local, Decimal("1200"))
        self.assertEqual(special_local, Decimal("1000"))

    def test_special_collapses_when_local_discount_is_below_threshold(self) -> None:
        """A local discount under the rounded percent threshold drops special."""
        offer = self._offer(price=Decimal("100"), list_price=Decimal("103"))

        price, special, cost, local, special_local = QuotationService._resolve_prices(
            _LinearCalculator(),
            offer,
            self.policy,
            Decimal("1"),
            self.settings,
        )

        self.assertEqual(price, Decimal("100"))
        self.assertEqual(special, Decimal("0"))
        self.assertEqual(cost, Decimal("100"))
        self.assertEqual(local, Decimal("1000"))
        self.assertEqual(special_local, Decimal("0"))

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
            weight_kg=Decimal("1"),
            courier=False,
            partida=None,
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
            weight_kg=Decimal("1"),
            courier=False,
            partida=None,
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
        strategy.resolve_exchange_rate.return_value = Decimal("7.75")
        strategy.calculate.return_value = CalculationResultDTO(
            cost_usd=Decimal("80"),
            price_local=Decimal("1000"),
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
        self.assertEqual(result.price_dolar, Decimal("100"))
        self.assertEqual(result.cost_dolar, Decimal("80"))
        self.assertEqual(result.cost_local, Decimal("620"))
        self.assertEqual(result.price_local, Decimal("1000"))
        self.assertEqual(result.special_price_dolar, Decimal("0"))
        self.assertEqual(result.offer_id, "")
        self.assertIsNone(result.delivery_promise_amz)
        strategy.resolve_delivery_promise.assert_not_called()
        strategy.calculate.assert_called_once()

    @staticmethod
    def _offer(
        price: Decimal,
        list_price: Decimal | None,
        guidance: str = "",
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
        )


if __name__ == "__main__":
    unittest.main()
