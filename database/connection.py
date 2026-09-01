"""Global MySQL connections reused across AWS Lambda invocations."""

from __future__ import annotations

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from config.settings import get_database_settings, get_settings
from Utils.logger import get_logger

logger = get_logger(__name__)

# Module-level cache lives outside lambda_handler. Warm execution environments
# keep these sockets and skip open/close on every invocation.
_connections: dict[str, Connection] = {}


def get_connection(country: str) -> Connection:
    """Return the process-wide MySQL connection configured for a country.

    Connections are cached independently so one warm Lambda can serve both
    GT and CR without opening a new socket per query or per request.

    Args:
        country: Request country used to select ``DB_GT_*`` or ``DB_CR_*``.

    Returns:
        Connection: Live pymysql connection for the selected country.

    Raises:
        pymysql.Error: If the database cannot be reached with current settings.
        ValueError: If the country configuration is missing.
    """
    normalized = country.strip().upper()
    connection = _connections.get(normalized)
    if connection is not None and connection.open:
        return connection
    return _open_connection(normalized)


def ensure_connection(country: str) -> Connection:
    """Reuse the global connection after a single liveness check.

    Call this once per invocation after the country is known. Idle MySQL
    servers may drop the socket while the Lambda container stays warm.

    Args:
        country: Request country used to select the cached connection.

    Returns:
        Connection: Live pymysql connection for the selected country.
    """
    normalized = country.strip().upper()
    connection = get_connection(normalized)
    try:
        connection.ping(reconnect=True)
        return connection
    except pymysql.Error:
        logger.warning(
            "Stale MySQL connection detected for %s. Reconnecting.",
            normalized,
        )
        _connections.pop(normalized, None)
        return _open_connection(normalized)


def warm_connections() -> None:
    """Open global connections during container init, not inside the handler.

    Failures are logged and ignored so a missing country database does not
    block Lambda initialization for the other country.
    """
    for country in get_settings().databases:
        try:
            get_connection(country)
        except Exception:
            logger.warning(
                "Could not warm the %s MySQL connection during init.",
                country,
                exc_info=True,
            )


def close_connection(country: str | None = None) -> None:
    """Close one country connection or every cached connection.

    Args:
        country: Optional country to close. ``None`` closes GT and CR.

    Returns:
        None: A later request opens the required connection again.
    """
    countries = [country.strip().upper()] if country else list(_connections)
    for country_code in countries:
        connection = _connections.pop(country_code, None)
        if connection is None:
            continue
        try:
            connection.close()
        except pymysql.Error:
            logger.warning(
                "Error while closing %s MySQL connection.",
                country_code,
                exc_info=True,
            )


def _open_connection(country: str) -> Connection:
    """Create and cache a new MySQL connection for one country.

    Args:
        country: Normalized country code.

    Returns:
        Connection: Newly opened pymysql connection.
    """
    settings = get_database_settings(country)
    connection = pymysql.connect(
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
        "Opened %s MySQL connection to %s:%s/%s",
        country,
        settings.db_host,
        settings.db_port,
        settings.db_name,
    )
    _connections[country] = connection
    return connection
