"""Factory that resolves a country Strategy."""

from collections.abc import Callable

from country.country_prefetched_strategy import CountryPrefetchedStrategy
from country.country_strategy_abstract import CountryQuotationStrategy

StrategyBuilder = Callable[[], CountryQuotationStrategy]


class CountryStrategyFactory:
    """Create registered country quotation Strategies."""

    _builders: dict[str, StrategyBuilder] = {}

    @classmethod
    def register(cls, country: str, builder: StrategyBuilder) -> None:
        """Register a country Strategy builder.

        Args:
            country: Uppercase country code.
            builder: Zero-argument callable that creates the Strategy.

        Returns:
            None: The process-local registry is updated.
        """
        cls._builders[country.upper()] = builder

    @classmethod
    def create(cls, country: str) -> CountryQuotationStrategy:
        """Create the Strategy registered for a country.

        Args:
            country: Requested country code.

        Returns:
            CountryQuotationStrategy: Country implementation wrapped so
                request-level reads can reuse a prefetched lookup.

        Raises:
            ValueError: If the country has no implementation.
        """
        cls._register_defaults()
        builder = cls._builders.get(country.upper())
        if builder is None:
            raise ValueError(f"Unsupported country: {country}.")
        return CountryPrefetchedStrategy(builder())

    @classmethod
    def _register_defaults(cls) -> None:
        """Register built-in countries once.

        Returns:
            None: GT and CR builders become available.
        """
        if cls._builders:
            return
        from country.CR.strategy import CostaRicaQuotationStrategy
        from country.GT.strategy import GuatemalaQuotationStrategy

        cls.register("GT", GuatemalaQuotationStrategy)
        cls.register("CR", CostaRicaQuotationStrategy)
