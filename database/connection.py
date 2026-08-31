"""MySQL connection factory for AWS Lambda warm starts."""

from __future__ import annotations

from typing import Optional

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from config.settings import get_settings
from Utils.logger import get_logger

logger = get_logger(__name__)

_connection: Optional[Connection] = None


def get_connection() -> Connection:
    """Return a reused MySQL connection with dictionary cursors.

    The connection is stored at module level so warm Lambda invocations
    skip the TCP handshake. A new one is opened if the previous is closed.

    Returns:
        Connection: Live pymysql connection. Do not close it after each
            query; use ``close_connection`` only on shutdown.

    Raises:
        pymysql.Error: If the database cannot be reached with current settings.
    """
    global _connection

    if _connection is not None and _connection.open:
        try:
            _connection.ping(reconnect=True)
            return _connection
        except pymysql.Error:
            logger.warning("Stale MySQL connection detected. Reconnecting.")
            _connection = None

    settings = get_settings()
    _connection = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        connect_timeout=settings.db_connect_timeout,
        cursorclass=DictCursor,
        charset="utf8mb4",
        autocommit=True,
    )
    logger.info(
        "Opened MySQL connection to %s:%s/%s",
        settings.db_host,
        settings.db_port,
        settings.db_name,
    )
    return _connection


def close_connection() -> None:
    """Close the cached connection if it exists.

    Returns:
        None: The next ``get_connection`` call opens a new session.
    """
    global _connection

    if _connection is not None:
        try:
            _connection.close()
        except pymysql.Error:
            logger.warning("Error while closing MySQL connection.", exc_info=True)
        _connection = None
