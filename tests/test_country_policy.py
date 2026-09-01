"""Tests for policy precedence and UNSPSC defaults."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from DTO.quotation_context_dto import (
    CountrySettingsDTO,
    ProductDataDTO,
    TariffDataDTO,
    UnspscDataDTO,
)
from DTO.quotation_request_dto import ProductQuotationDTO
from repository.base_quotation_repository import BaseQuotationRepository
from service.quotation_service import QuotationService


class _TestRepository(BaseQuotationRepository):
    """Concrete shared repository used to verify country routing."""

    COUNTRY = "GT"


class _RestrictedStrategy:
    """Fake granular Strategy exposing a restrictive tariff."""

    def get_product_data(self, product_id):
        """Return a stored tariff code."""
        return ProductDataDTO(
            weight_kg=Decimal("1"),
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
        self.assertEqual(cursor.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
