"""Tests for policy precedence and UNSPSC defaults."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from country.country_prefetched_strategy import CountryPrefetchedStrategy
from country.CR.strategy import CostaRicaQuotationStrategy
from country.GT.strategy import GuatemalaQuotationStrategy
from DTO.quotation_context_dto import (
    CountrySettingsDTO,
    ProductDataDTO,
    QuotationLookupDTO,
    ResolvedPolicyDTO,
    TariffDataDTO,
    UnspscDataDTO,
)
from DTO.quotation_request_dto import ProductQuotationDTO
from repository.base_quotation_repository import BaseQuotationRepository
from service.quotation_orchestrator import QuotationOrchestrator
from service.quotation_service import QuotationService


class _TestRepository(BaseQuotationRepository):
    """Concrete shared repository used to verify country routing."""

    COUNTRY = "GT"


class _RestrictedStrategy:
    """Fake granular Strategy exposing a restrictive tariff."""

    def get_product_data(self, product_id):
        """Return a stored tariff code."""
        return ProductDataDTO(
            weight_lb=Decimal("1"),
            courier=False,
            partida="001",
        )

    def get_unspsc_data(self, unspsc, settings):
        """Return an unrestricted UNSPSC."""
        return UnspscDataDTO(
            arancel_percentage=Decimal("0.15"),
            restriction=0,
            category_code=0,
            margin_percentage=Decimal("1.1"),
            danger_good_active=False,
            courier=False,
        )

    def resolve_tariff_data(self, partida):
        """Return a restrictive tariff that must override UNSPSC."""
        return TariffDataDTO(
            partida="001",
            arancel_percentage=Decimal("0.05"),
            courier=False,
            restriction=1,
        )

    def get_category_tree_courier(self, product_id):
        """A tariff prevents category-tree refinement."""
        raise AssertionError("Category tree must not run when GT has a tariff.")

    def resolve_sales_iva_rate(self, cabys, settings):
        """Return a dummy rate; this test fails before the calculator."""
        return Decimal("0.12")


class CountryPolicyTests(unittest.TestCase):
    """Verify source precedence before the calculators execute."""

    def test_gt_tariff_restriction_returns_policy_fields(self) -> None:
        """A non-courier GT tariff overrides UNSPSC restriction."""
        settings = CountrySettingsDTO(
            {
                "margen": "1.1",
                "max_product_weight_kg": "100",
            }
        )
        product = ProductQuotationDTO(
            product_id=10,
            amz_weight_kg=Decimal("1"),
            unspsc="123",
            amz_offers=(),
        )

        result = QuotationService()._quote_product(
            _RestrictedStrategy(),
            product,
            settings,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.restriction, 1)
        self.assertEqual(result.partida, "001")

    @patch("repository.base_quotation_repository.get_connection")
    def test_unknown_unspsc_is_recorded_and_uses_settings(self, connection) -> None:
        """Missing UNSPSC data is inserted and never uses code defaults."""
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        connection.return_value.cursor.return_value.__enter__.return_value = cursor
        settings = CountrySettingsDTO(
            {
                "default_arancel": "0.25",
                "default_restriction": "2",
                "default_arancel_category_cod": "99",
                "margen": "1.3",
                "default_danger_good_active": "1",
                "default_courier": "1",
            }
        )

        repository = _TestRepository()
        result = repository.find_unspsc("999", settings)
        repository.save_unknown_unspsc("999")
        default = repository.get_default_unspsc(settings)

        self.assertIsNone(result)
        self.assertEqual(default.arancel_percentage, Decimal("0.25"))
        self.assertEqual(default.restriction, 2)
        self.assertTrue(default.courier)
        self.assertEqual(cursor.execute.call_count, 1)
        cursor.executemany.assert_called_once()

    @patch("repository.base_quotation_repository.get_connection")
    def test_unknown_unspsc_insert_failure_does_not_abort(self, connection) -> None:
        """A failed unknown-UNSPSC insert is logged and does not raise."""
        cursor = MagicMock()
        cursor.executemany.side_effect = Exception("read-only transaction")
        connection.return_value.cursor.return_value.__enter__.return_value = cursor

        with self.assertLogs(
            "repository.base_quotation_repository", level="WARNING"
        ) as logs:
            _TestRepository().save_unknown_unspsc("999")

        self.assertTrue(
            any("Could not record unknown UNSPSC" in line for line in logs.output)
        )

    def test_null_unspsc_uses_defaults_without_recording_unknown(self) -> None:
        """A null UNSPSC applies country defaults and skips unknown inserts."""
        default = UnspscDataDTO(
            arancel_percentage=Decimal("0.25"),
            restriction=0,
            category_code=0,
            margin_percentage=Decimal("1.2"),
            danger_good_active=False,
            courier=False,
        )
        strategy = MagicMock()
        strategy.get_product_data.return_value = ProductDataDTO(
            weight_lb=Decimal("1"),
            courier=False,
            partida=None,
        )
        strategy.get_default_unspsc.return_value = default
        strategy.resolve_tariff_data.return_value = None
        strategy.get_category_tree_courier.return_value = False
        strategy.resolve_sales_iva_rate.return_value = Decimal("0.13")
        product = ProductQuotationDTO(
            product_id=10,
            amz_weight_kg=Decimal("1"),
            unspsc=None,
            amz_offers=(),
        )

        policy = QuotationOrchestrator().resolve_policy(
            strategy,
            product,
            CountrySettingsDTO({"margen": "1.2"}),
        )

        self.assertEqual(policy.arancel_percentage, Decimal("0.25"))
        self.assertTrue(
            any("no tiene UNSPSC" in note for note in policy.quotation_notes)
        )
        strategy.get_unspsc_data.assert_not_called()
        strategy.save_unknown_unspsc.assert_not_called()
        strategy.get_default_unspsc.assert_called_once()

    def test_null_product_id_skips_oc_product_and_category_tree(self) -> None:
        """A null product_id uses other sources without querying oc_product."""
        default = UnspscDataDTO(
            arancel_percentage=Decimal("0.25"),
            restriction=0,
            category_code=0,
            margin_percentage=Decimal("1.2"),
            danger_good_active=False,
            courier=True,
        )
        strategy = MagicMock()
        strategy.get_product_data.return_value = ProductDataDTO(
            weight_lb=Decimal("99"),
            courier=True,
            partida="stored",
        )
        strategy.get_unspsc_data.return_value = default
        strategy.resolve_tariff_data.return_value = None
        strategy.get_category_tree_courier.return_value = True
        strategy.resolve_sales_iva_rate.return_value = Decimal("0.13")
        product = ProductQuotationDTO(
            product_id=None,
            amz_weight_kg=Decimal("1"),
            unspsc="123",
            amz_offers=(),
            pac_product_partida="0012",
            pac_product_courier=False,
            pac_product_weight=Decimal("2"),
        )

        policy = QuotationOrchestrator().resolve_policy(
            strategy,
            product,
            CountrySettingsDTO({"margen": "1.2"}),
        )

        strategy.get_product_data.assert_called_once_with(None)
        strategy.get_category_tree_courier.assert_not_called()
        self.assertEqual(policy.partida, "0012")
        self.assertTrue(policy.courier)
        self.assertTrue(
            any("product_id es null" in note for note in policy.quotation_notes)
        )
        self.assertTrue(
            any(
                "override del request" in note
                for note in policy.quotation_notes
            )
        )

    def test_prefetch_skips_null_product_id_queries(self) -> None:
        """Prefetch does not load oc_product rows for null product_id values."""
        unspsc_data = UnspscDataDTO(
            arancel_percentage=Decimal("0.25"),
            restriction=0,
            category_code=0,
            margin_percentage=Decimal("1.2"),
            danger_good_active=False,
            courier=False,
        )
        strategy = MagicMock()
        strategy.load_products.return_value = {}
        strategy.load_unspsc_map.return_value = {"123": unspsc_data}
        strategy.load_tariffs.return_value = {}
        strategy.load_sales_iva_map.return_value = {}
        products = (
            ProductQuotationDTO(
                product_id=None,
                amz_weight_kg=Decimal("1"),
                unspsc="123",
                amz_offers=(),
            ),
        )
        settings = CountrySettingsDTO({"margen": "1.2"})

        lookup = QuotationOrchestrator().prefetch(strategy, products, settings)

        strategy.load_products.assert_called_once_with(())
        strategy.load_category_tree_courier_map.assert_not_called()
        self.assertEqual(lookup.products, {})
        self.assertEqual(lookup.category_courier, {})

    def test_weight_limit_is_compared_in_kilograms(self) -> None:
        """The max weight setting is kilograms, not the stored pound value."""
        settings = CountrySettingsDTO({"max_product_weight_kg": "10"})
        under_limit = ResolvedPolicyDTO(
            weight_lb=Decimal("15"),
            arancel_percentage=Decimal("0.15"),
            margin_percentage=Decimal("1.1"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
            sales_iva_rate=Decimal("0.12"),
        )
        over_limit = ResolvedPolicyDTO(
            weight_lb=Decimal("25"),
            arancel_percentage=Decimal("0.15"),
            margin_percentage=Decimal("1.1"),
            courier=False,
            restriction=0,
            danger_good_active=False,
            partida=None,
            sales_iva_rate=Decimal("0.12"),
        )

        allowed = QuotationOrchestrator.validate_policy(10, under_limit, settings)
        rejected = QuotationOrchestrator.validate_policy(10, over_limit, settings)

        self.assertIsNone(allowed)
        self.assertIsNotNone(rejected)
        self.assertFalse(rejected.success)
        self.assertEqual(
            rejected.message,
            "Product exceeds the configured weight limit (compared in KG).",
        )


class PrefetchedSalesIvaTests(unittest.TestCase):
    """Verify country resolvers consume prefetched CABYS rates."""

    def _settings(self) -> CountrySettingsDTO:
        return CountrySettingsDTO({"default_iva_venta": "0.13"})

    def _lookup(self, sales_iva: dict) -> QuotationLookupDTO:
        return QuotationLookupDTO(
            products={},
            unspsc={},
            tariffs={},
            category_courier={},
            sales_iva=sales_iva,
        )

    def test_cr_uses_prefetched_cabys_rate_without_repository(self) -> None:
        """CR applies the prefetch map and skips pac_cabys."""
        repository = MagicMock()
        strategy = CountryPrefetchedStrategy(
            CostaRicaQuotationStrategy(repository=repository),
            self._lookup({"1234567890123": Decimal("0.01")}),
        )

        result = strategy.resolve_sales_iva_rate("1234567890123", self._settings())

        self.assertEqual(result.rate, Decimal("0.01"))
        self.assertEqual(
            result.quotation_notes,
            (
                "IVA de venta 0.01 tomado de pac_cabys para CABYS 1234567890123.",
            ),
        )
        repository.get_cabys_tax_rates.assert_not_called()

    def test_cr_prefetched_miss_uses_default_without_repository(self) -> None:
        """CR missing CABYS in the prefetch map uses default_iva_venta."""
        repository = MagicMock()
        strategy = CountryPrefetchedStrategy(
            CostaRicaQuotationStrategy(repository=repository),
            self._lookup({}),
        )

        result = strategy.resolve_sales_iva_rate("1234567890123", self._settings())

        self.assertEqual(result.rate, Decimal("0.13"))
        self.assertTrue(
            any("no encontrado en pac_cabys" in note for note in result.quotation_notes)
        )
        repository.get_cabys_tax_rates.assert_not_called()

    def test_gt_ignores_prefetched_cabys_and_keeps_setting_notes(self) -> None:
        """GT keeps oc_setting IVA even when a CABYS map is prefetched."""
        repository = MagicMock()
        strategy = CountryPrefetchedStrategy(
            GuatemalaQuotationStrategy(repository=repository),
            self._lookup({"1234567890123": Decimal("0.01")}),
        )

        result = strategy.resolve_sales_iva_rate("1234567890123", self._settings())

        self.assertEqual(result.rate, Decimal("0.13"))
        self.assertEqual(
            result.quotation_notes,
            (
                "IVA de venta GT desde oc_setting default_iva_venta (0.13).",
            ),
        )


if __name__ == "__main__":
    unittest.main()
