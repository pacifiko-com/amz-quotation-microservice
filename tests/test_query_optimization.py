"""Tests for settings cache and batched repository reads."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from config.settings import get_settings as load_app_settings
from country.CR.repository.cr_quotation_repository import CostaRicaQuotationRepository
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
        load_app_settings.cache_clear()

    @patch("country.GT.strategy.GuatemalaQuotationRepository.get_settings")
    def test_settings_are_loaded_once_per_process(self, repository_get_settings) -> None:
        """Warm invocations reuse oc_setting without a second query."""
        repository_get_settings.return_value = CountrySettingsDTO({"margen": "1.1"})

        first = GuatemalaQuotationStrategy().resolve_settings()
        second = GuatemalaQuotationStrategy().resolve_settings()

        self.assertIs(first, second)
        repository_get_settings.assert_called_once()

    @patch("country.GT.strategy.GuatemalaQuotationRepository.get_settings")
    def test_settings_are_reloaded_after_ttl_expires(
        self,
        repository_get_settings,
    ) -> None:
        """Expired oc_setting values are fetched again from MySQL."""
        repository_get_settings.side_effect = [
            CountrySettingsDTO({"margen": "1.1"}),
            CountrySettingsDTO({"margen": "1.2"}),
        ]
        clock = {"now": 0.0}

        with patch.dict(os.environ, {"OC_SETTING_CACHE_TTL_SECONDS": "60"}):
            load_app_settings.cache_clear()
            with patch(
                "database.settings_cache.monotonic",
                side_effect=lambda: clock["now"],
            ):
                first = GuatemalaQuotationStrategy().resolve_settings()
                clock["now"] = 59.0
                still_fresh = GuatemalaQuotationStrategy().resolve_settings()
                clock["now"] = 60.0
                expired = GuatemalaQuotationStrategy().resolve_settings()

        self.assertIs(first, still_fresh)
        self.assertIsNot(first, expired)
        self.assertEqual(expired.require("margen"), "1.2")
        self.assertEqual(repository_get_settings.call_count, 2)

    @patch("country.GT.strategy.GuatemalaQuotationRepository.get_settings")
    def test_settings_are_not_reused_when_ttl_is_zero(
        self,
        repository_get_settings,
    ) -> None:
        """TTL zero disables reuse between handler invocations."""
        repository_get_settings.side_effect = [
            CountrySettingsDTO({"margen": "1.1"}),
            CountrySettingsDTO({"margen": "1.2"}),
        ]

        with patch.dict(os.environ, {"OC_SETTING_CACHE_TTL_SECONDS": "0"}):
            load_app_settings.cache_clear()
            first = GuatemalaQuotationStrategy().resolve_settings()
            second = GuatemalaQuotationStrategy().resolve_settings()

        self.assertIsNot(first, second)
        self.assertEqual(repository_get_settings.call_count, 2)

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

    @patch("country.GT.repository.gt_quotation_repository.get_connection")
    def test_gt_tariff_null_courier_and_restriction_are_preserved(
        self,
        connection,
    ) -> None:
        """NULL restriccion keeps the batch; NULL courier stays None."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {
                "partida": "001",
                "arancel_porcentaje": None,
                "courier": None,
                "restriccion": None,
            },
            {
                "partida": "002",
                "arancel_porcentaje": "0.05",
                "courier": 1,
                "restriccion": 0,
            },
        ]
        connection.return_value.cursor.return_value.__enter__.return_value = cursor

        result = GuatemalaQuotationRepository().get_tariffs(("001", "002"))

        self.assertIsNone(result["001"].courier)
        self.assertIsNone(result["001"].restriction)
        self.assertTrue(result["002"].courier)
        self.assertEqual(result["002"].restriction, 0)
        self.assertEqual(len(result), 2)

    @patch("country.CR.repository.cr_quotation_repository.get_connection")
    def test_cr_tariff_null_courier_and_restriction_are_preserved(
        self,
        connection,
    ) -> None:
        """NULL restriccion keeps the batch; NULL courier stays None."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {
                "partida": "001",
                "dai": None,
                "isc": None,
                "courier": None,
                "restriccion": None,
            },
            {
                "partida": "002",
                "dai": "0.05",
                "isc": "0",
                "courier": 1,
                "restriccion": 0,
            },
        ]
        connection.return_value.cursor.return_value.__enter__.return_value = cursor

        result = CostaRicaQuotationRepository().get_tariffs(("001", "002"))

        self.assertIsNone(result["001"].courier)
        self.assertIsNone(result["001"].restriction)
        self.assertTrue(result["002"].courier)
        self.assertEqual(result["002"].restriction, 0)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
