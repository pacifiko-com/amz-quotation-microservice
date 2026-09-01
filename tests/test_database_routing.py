"""Tests for country-aware configuration and warm connection reuse."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config.settings import get_database_settings, get_settings
from database import connection


class DatabaseRoutingTests(unittest.TestCase):
    """Verify one Lambda can route GT and CR to independent databases."""

    def tearDown(self) -> None:
        """Clear process caches between tests."""
        get_settings.cache_clear()
        connection._connections.clear()

    def test_prefixed_environment_selects_country_database(self) -> None:
        """GT and CR settings are loaded from different variable prefixes."""
        environment = {
            "DB_GT_HOST": "gt-host",
            "DB_GT_NAME": "gt-db",
            "DB_GT_USER": "gt-user",
            "DB_CR_HOST": "cr-host",
            "DB_CR_NAME": "cr-db",
            "DB_CR_USER": "cr-user",
        }
        with patch.dict(os.environ, environment, clear=True):
            get_settings.cache_clear()
            gt = get_database_settings("GT")
            cr = get_database_settings("CR")

        self.assertEqual(gt.db_host, "gt-host")
        self.assertEqual(cr.db_host, "cr-host")
        self.assertNotEqual(gt.db_name, cr.db_name)

    @patch("database.connection.pymysql.connect")
    @patch("database.connection.get_database_settings")
    def test_connections_are_cached_independently(self, settings, connect) -> None:
        """Warm requests reuse only the connection for their own country."""
        settings.side_effect = lambda country: SimpleNamespace(
            db_host=f"{country.lower()}-host",
            db_port=3306,
            db_name=f"{country.lower()}-db",
            db_user="user",
            db_password="password",
            db_connect_timeout=10,
        )
        gt_connection = MagicMock()
        gt_connection.open = True
        cr_connection = MagicMock()
        cr_connection.open = True
        connect.side_effect = [gt_connection, cr_connection]

        first_gt = connection.get_connection("GT")
        selected_cr = connection.get_connection("CR")
        second_gt = connection.get_connection("GT")

        self.assertIs(first_gt, second_gt)
        self.assertIsNot(first_gt, selected_cr)
        self.assertEqual(connect.call_count, 2)
        gt_connection.ping.assert_not_called()

    @patch("database.connection.pymysql.connect")
    @patch("database.connection.get_database_settings")
    def test_ensure_connection_pings_once(self, settings, connect) -> None:
        """The handler checks liveness once; later queries reuse the socket."""
        settings.return_value = SimpleNamespace(
            db_host="gt-host",
            db_port=3306,
            db_name="gt-db",
            db_user="user",
            db_password="password",
            db_connect_timeout=10,
        )
        gt_connection = MagicMock()
        gt_connection.open = True
        connect.return_value = gt_connection

        first = connection.ensure_connection("GT")
        second = connection.get_connection("GT")

        self.assertIs(first, second)
        self.assertEqual(connect.call_count, 1)
        gt_connection.ping.assert_called_once_with(reconnect=True)


if __name__ == "__main__":
    unittest.main()
