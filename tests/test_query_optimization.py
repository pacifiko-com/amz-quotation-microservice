"""Tests for settings cache and batched repository reads."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from country.GT.repository.gt_quotation_repository import GuatemalaQuotationRepository
from country.GT.strategy import GuatemalaQuotationStrategy
from database.settings_cache import clear_settings_cache
from DTO.quotation_context_dto import CountrySettingsDTO
from repository.base_quotation_repository import BaseQuotationRepository


class _SharedRepository(BaseQuotationRepository):
    """Concrete shared repository used for batch SQL tests."""

    COUNTRY = "GT"


class QueryOptimizationTests(unittest.TestCase):
    """Verify warm settings reuse and IN-clause batching."""

    def tearDown(self) -> None:
        """Drop cached oc_setting values between tests."""
        clear_settings_cache()

    @patch("country.GT.strategy.GuatemalaQuotationRepository.get_settings")
    def test_settings_are_loaded_once_per_process(self, get_settings) -> None:
        """Warm invocations reuse oc_setting without a second query."""
        get_settings.return_value = CountrySettingsDTO({"margen": "1.1"})

        first = GuatemalaQuotationStrategy().resolve_settings()
        second = GuatemalaQuotationStrategy().resolve_settings()

        self.assertIs(first, second)
        get_settings.assert_called_once()

    @patch("repository.base_quotation_repository.get_connection")
    def test_unspsc_codes_are_loaded_in_one_query(self, connection) -> None:
        """Distinct UNSPSC codes share a single IN lookup."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        connection.return_value.cursor.return_value.__enter__.return_value = cursor
        settings = CountrySettingsDTO({})

        result = _SharedRepository().find_unspsc_map(("111", "222", "111"), settings)

        self.assertEqual(result, {"111": None, "222": None})
        self.assertEqual(cursor.execute.call_count, 1)
        sql, params = cursor.execute.call_args.args
        self.assertIn("IN", sql)
        self.assertEqual(params, ("111", "222"))

    @patch("country.GT.repository.gt_quotation_repository.get_connection")
    def test_products_are_loaded_in_one_query(self, connection) -> None:
        """Distinct product identifiers share a single IN lookup."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        connection.return_value.cursor.return_value.__enter__.return_value = cursor

        GuatemalaQuotationRepository().get_products((10, 11, 10))

        self.assertEqual(cursor.execute.call_count, 1)
        sql, params = cursor.execute.call_args.args
        self.assertIn("IN", sql)
        self.assertEqual(params, (10, 11))


if __name__ == "__main__":
    unittest.main()
