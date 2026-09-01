"""Tests for the Lambda request/response orchestration contract."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from country.country_strategy_factory import CountryStrategyFactory
from DTO.quotation_context_dto import CountrySettingsDTO
from DTO.quotation_request_dto import QuotationRequestDTO
from DTO.quotation_response_dto import ResultObjectDTO
from service.quotation_service import QuotationService


class _PartialStrategy:
    """Test Strategy that only supplies request-level settings."""

    @staticmethod
    def resolve_settings():
        """Return settings because the product flow is overridden."""
        return CountrySettingsDTO({})


class _PartialQuotationService(QuotationService):
    """Test service that isolates result-loop behavior."""

    def _quote_product(self, strategy, product, settings, prefer_amazon_fulfillment=False):
        """Return or raise according to product id."""
        if product.product_id == 2:
            raise ValueError("Expected test failure.")
        return ResultObjectDTO(
            success=True,
            message="ok",
            product_id=product.product_id,
        )


class ContractTests(unittest.TestCase):
    """Validate public DTO parsing and partial processing."""

    def test_country_strategies_expose_granular_contract(self) -> None:
        """Country Strategies do not own the complete product flow."""
        required_operations = (
            "resolve_settings",
            "get_unspsc_data",
            "save_unknown_unspsc",
            "get_default_unspsc",
            "get_product_data",
            "resolve_tariff_data",
            "get_category_tree_courier",
            "resolve_exchange_rate",
            "calculate",
            "resolve_delivery_promise",
        )
        for country in ("GT", "CR"):
            strategy = CountryStrategyFactory.create(country)
            self.assertFalse(hasattr(strategy, "quote_product"))
            self.assertTrue(
                all(callable(getattr(strategy, name)) for name in required_operations)
            )

    def test_request_preserves_partida_as_string(self) -> None:
        """Tariff codes retain leading zeros."""
        request = QuotationRequestDTO.from_event(
            {
                "body": {
                    "country": "GT",
                    "products": [
                        {
                            "product_id": 1,
                            "amz_weight_kg": 1,
                            "unspsc": "123",
                            "amz_offers": [],
                            "pac_product_partida": "0012",
                        }
                    ],
                }
            }
        )

        self.assertEqual(request.products[0].pac_product_partida, "0012")
        self.assertFalse(request.prefer_amazon_fulfillment)

    def test_request_reads_prefer_amazon_fulfillment(self) -> None:
        """The optional AF-first flag is accepted at request level."""
        request = QuotationRequestDTO.from_event(
            {
                "body": {
                    "country": "GT",
                    "prefer_amazon_fulfillment": True,
                    "products": [
                        {
                            "product_id": 1,
                            "amz_weight_kg": 1,
                            "unspsc": "123",
                            "amz_offers": [],
                        }
                    ],
                }
            }
        )

        self.assertTrue(request.prefer_amazon_fulfillment)

    @patch("service.quotation_service.CountryStrategyFactory.create")
    def test_partial_results_keep_input_order(self, create) -> None:
        """A failed product does not stop later products."""
        create.return_value = _PartialStrategy()
        request = QuotationRequestDTO.from_event(
            {
                "body": {
                    "country": "GT",
                    "products": [
                        {
                            "product_id": product_id,
                            "amz_weight_kg": 1,
                            "unspsc": "123",
                            "amz_offers": [],
                        }
                        for product_id in (1, 2, 3)
                    ],
                }
            }
        )

        response = _PartialQuotationService().quote(request)

        self.assertFalse(response.success)
        self.assertEqual([item.product_id for item in response.result], [1, 2, 3])
        self.assertEqual([item.success for item in response.result], [True, False, True])


if __name__ == "__main__":
    unittest.main()
