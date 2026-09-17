"""Runtime settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from config.secrets_loader import apply_secrets_from_manager
from const import COUNTRIES


@dataclass(frozen=True)
class DatabaseSettings:
    """MySQL settings for one country.

    Attributes:
        db_host (str): MySQL hostname or IP.
        db_port (int): MySQL TCP port.
        db_name (str): Target schema name.
        db_user (str): Database user.
        db_password (str): Database password. Never log this value.
        db_connect_timeout (int): Seconds to wait when opening a connection.
    """

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_connect_timeout: int


@dataclass(frozen=True)
class Settings:
    """Application settings for a single multi-country Lambda.

    Attributes:
        databases: MySQL configuration keyed by country code.
        log_level: Logging verbosity (DEBUG, INFO, WARNING, ERROR).
        oc_setting_cache_ttl_seconds: Seconds to reuse ``oc_setting`` in
            process memory before reloading from MySQL.
        quotation_worker_threads: Maximum product-quote threads after prefetch.
        quotation_min_products_per_worker: Minimum products assigned to each
            worker; extra threads are dropped when the batch is smaller.
    """

    databases: dict[str, DatabaseSettings]
    log_level: str
    oc_setting_cache_ttl_seconds: int
    quotation_worker_threads: int
    quotation_min_products_per_worker: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache settings from the process environment.

    Returns:
        Settings: Frozen configuration reused across warm Lambda invocations.
    """
    apply_secrets_from_manager()
    return Settings(
        databases={
            country: _load_database_settings(country)
            for country in COUNTRIES
        },
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        oc_setting_cache_ttl_seconds=_load_oc_setting_cache_ttl_seconds(),
        quotation_worker_threads=_load_positive_int(
            "QUOTATION_WORKER_THREADS", 10
        ),
        quotation_min_products_per_worker=_load_positive_int(
            "QUOTATION_MIN_PRODUCTS_PER_WORKER", 10
        ),
    )


def _load_positive_int(name: str, default: int) -> int:
    """Parse a positive integer from the environment.

    Args:
        name: Environment variable name.
        default: Value used when the variable is missing or invalid.

    Returns:
        int: Configured positive integer.
    """
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def quote_worker_count(
    product_count: int,
    max_workers: int,
    min_products_per_worker: int,
) -> int:
    """Choose how many quote threads to start for one request.

    Args:
        product_count: Products in the current request.
        max_workers: Configured thread cap.
        min_products_per_worker: Minimum products each worker should own.

    Returns:
        int: At least one worker, never more than the product count or cap.
    """
    if product_count <= 1:
        return 1
    workers = max(1, max_workers)
    minimum = max(1, min_products_per_worker)
    by_load = max(1, product_count // minimum)
    return min(workers, by_load, product_count)


def _load_oc_setting_cache_ttl_seconds() -> int:
    """Parse cache TTL from the environment, defaulting to one hour.

    Returns:
        int: Seconds to keep ``oc_setting`` values. ``0`` or a negative
            value disables reuse between calls.
    """
    raw_value = os.getenv("OC_SETTING_CACHE_TTL_SECONDS", "300")
    try:
        return int(raw_value)
    except ValueError:
        return 300


def get_database_settings(country: str) -> DatabaseSettings:
    """Return the database configuration selected by request country.

    Args:
        country: Country code received by the Lambda.

    Returns:
        DatabaseSettings: Credentials and connection options for that country.

    Raises:
        ValueError: If the country is unsupported.
        ConfigurationError: If required database fields are missing.
    """
    from Utils.exceptions import ConfigurationError

    normalized = country.strip().upper()
    database = get_settings().databases.get(normalized)
    if database is None:
        raise ValueError(f"Unsupported database country: {country}.")
    missing = [
        name
        for name, value in (
            ("HOST", database.db_host),
            ("NAME", database.db_name),
            ("USER", database.db_user),
        )
        if not value
    ]
    if missing:
        fields = ", ".join(f"DB_{normalized}_{name}" for name in missing)
        raise ConfigurationError(f"Missing database configuration: {fields}.")
    return database


def _load_database_settings(country: str) -> DatabaseSettings:
    """Load one country's prefixed database environment variables.

    Args:
        country: Uppercase country code used in variable names.

    Returns:
        DatabaseSettings: Parsed ``DB_<COUNTRY>_*`` values.
    """
    prefix = f"DB_{country}_"
    return DatabaseSettings(
        db_host=os.getenv(f"{prefix}HOST", ""),
        db_port=int(os.getenv(f"{prefix}PORT", "3306")),
        db_name=os.getenv(f"{prefix}NAME", ""),
        db_user=os.getenv(f"{prefix}USER", ""),
        db_password=os.getenv(f"{prefix}PASSWORD", ""),
        db_connect_timeout=int(os.getenv(f"{prefix}CONNECT_TIMEOUT", "10")),
    )
