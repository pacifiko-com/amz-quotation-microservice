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
    """

    databases: dict[str, DatabaseSettings]
    log_level: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache settings from the process environment.

    Returns:
        Settings: Frozen configuration reused across warm Lambda invocations.
    """
    return Settings(
        databases={
            country: _load_database_settings(country)
            for country in ("GT", "CR")
        },
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def get_database_settings(country: str) -> DatabaseSettings:
    """Return the database configuration selected by request country.

    Args:
        country: Country code received by the Lambda.

    Returns:
        DatabaseSettings: Credentials and connection options for that country.

    Raises:
        ValueError: If the country is unsupported or its configuration is
            incomplete.
    """
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
        raise ValueError(f"Missing database configuration: {fields}.")
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
        db_host=os.getenv(f"{prefix}HOST", "localhost"),
        db_port=int(os.getenv(f"{prefix}PORT", "3309")),
        db_name=os.getenv(f"{prefix}NAME", "db_pacifiko"),
        #db_name=os.getenv(f"{prefix}NAME", "CRProdDb"),
        db_user=os.getenv(f"{prefix}USER", "dmorales"),
        db_password=os.getenv(f"{prefix}PASSWORD", ''),
        db_connect_timeout=int(os.getenv(f"{prefix}CONNECT_TIMEOUT", "10")),
    )
