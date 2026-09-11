"""Process-wide cache of ``oc_setting`` values for warm Lambda reuse."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from DTO.quotation_context_dto import CountrySettingsDTO
from Utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _CachedSettings:
    """One country's settings plus the monotonic time they were stored."""

    settings: CountrySettingsDTO
    loaded_at: float


_settings: dict[str, _CachedSettings] = {}


def get_cached_settings(country: str) -> CountrySettingsDTO | None:
    """Return cached country settings when they were already loaded.

    Args:
        country: Country code used as cache key.

    Returns:
        CountrySettingsDTO | None: Cached values, or ``None`` to reload.
    """
    cached = _settings.get(country.strip().upper())
    if cached is None or not _is_fresh(cached):
        return None
    return cached.settings


def store_settings(country: str, settings: CountrySettingsDTO) -> None:
    """Keep country settings in module-level memory.

    Args:
        country: Country code used as cache key.
        settings: Loaded ``global_store`` values.

    Returns:
        None: Later requests reuse these values without querying MySQL.
    """
    _settings[country.strip().upper()] = _CachedSettings(
        settings=settings,
        loaded_at=monotonic(),
    )


def resolve_cached_settings(
    country: str,
    loader: Callable[[], CountrySettingsDTO],
) -> CountrySettingsDTO:
    """Return cached settings, loading them only when missing or expired.

    Args:
        country: Country code used as cache key.
        loader: Database read executed only on a cache miss.

    Returns:
        CountrySettingsDTO: Cached or freshly loaded settings.
    """
    normalized = country.strip().upper()
    cached = _settings.get(normalized)
    if cached is not None and _is_fresh(cached):
        return cached.settings
    settings = loader()
    _settings[normalized] = _CachedSettings(
        settings=settings,
        loaded_at=monotonic(),
    )
    logger.info("Loaded %s oc_setting values into the process cache.", normalized)
    return settings


def clear_settings_cache(country: str | None = None) -> None:
    """Drop one country cache entry or the whole process cache.

    Args:
        country: Optional country to drop. ``None`` clears GT and CR.

    Returns:
        None: The next resolve reloads from MySQL.
    """
    if country is None:
        _settings.clear()
        return
    _settings.pop(country.strip().upper(), None)


def warm_country_settings() -> None:
    """Load GT and CR settings during container init, not inside the handler.

    Failures are logged so a missing country database does not block init.
    """
    from country.country_strategy_factory import CountryStrategyFactory
    from config.settings import get_settings

    for country in get_settings().databases:
        try:
            CountryStrategyFactory.create(country).resolve_settings()
        except Exception:
            logger.warning(
                "Could not warm %s oc_setting cache during init.",
                country,
                exc_info=True,
            )


def _is_fresh(cached: _CachedSettings) -> bool:
    """Return whether cached settings are still inside the configured TTL.

    Args:
        cached: Settings entry with the time it was stored.

    Returns:
        bool: ``False`` when TTL is zero/negative or the age exceeds TTL.
    """
    ttl_seconds = _cache_ttl_seconds()
    if ttl_seconds <= 0:
        return False
    return (monotonic() - cached.loaded_at) < ttl_seconds


def _cache_ttl_seconds() -> int:
    """Read the process TTL from application settings.

    Returns:
        int: Seconds configured in ``OC_SETTING_CACHE_TTL_SECONDS``.
    """
    from config.settings import get_settings

    return get_settings().oc_setting_cache_ttl_seconds
