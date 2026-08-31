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
class Settings:
    """Database and log settings.

    Attributes:
        db_host (str): MySQL hostname or IP.
        db_port (int): MySQL TCP port.
        db_name (str): Target schema name.
        db_user (str): Database user.
        db_password (str): Database password. Never log this value.
        db_connect_timeout (int): Seconds to wait when opening a connection.
        log_level (str): Logging verbosity (DEBUG, INFO, WARNING, ERROR).
    """

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_connect_timeout: int
    log_level: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache settings from the process environment.

    Returns:
        Settings: Frozen configuration reused across warm Lambda invocations.
    """
    return Settings(
        db_host=os.getenv("DB_HOST", "localhost"),
        db_port=int(os.getenv("DB_PORT", "3306")),
        db_name=os.getenv("DB_NAME", "qa"),
        db_user=os.getenv("DB_USER", "root"),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
