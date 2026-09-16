"""Read-through wrapper that serves batched MySQL rows for one request."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    EMPTY_PRODUCT_DATA,
    ProductDataDTO,
    QuotationLookupDTO,
    DeliveryPromiseDTO,
    SalesIvaDTO,
    SelectedOfferDTO,
    TariffDataDTO,
    UnspscDataDTO,
)


class CountryPrefetchedStrategy(CountryQuotationStrategy):
    """Serve prefetched rows while delegating country formulas to the inner Strategy."""

    def __init__(
        self,
        inner: CountryQuotationStrategy,
        lookup: QuotationLookupDTO | None = None,
    ) -> None:
        """Create a read-through wrapper around a country Strategy.

        Args:
            inner: Country Strategy that owns data access and formulas.
            lookup: Optional rows loaded before the product loop.
        """
        self._inner = inner
        self._lookup = lookup
        self._exchange_rate: Decimal | None = None

    def bind_lookup(self, lookup: QuotationLookupDTO) -> None:
        """Attach batched rows so later reads skip MySQL.

        Args:
            lookup: Maps produced by the request-level prefetch.
        """
        self._lookup = lookup

    def resolve_settings(self) -> CountrySettingsDTO:
        """Delegate settings resolution to the country Strategy."""
        return self._inner.resolve_settings()

    def get_unspsc_data(
        self,
        unspsc: str,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO | None:
        """Return the prefetched UNSPSC row or ask the country Strategy."""
        if self._lookup is not None and unspsc in self._lookup.unspsc:
            return self._lookup.unspsc[unspsc]
        return self._inner.get_unspsc_data(unspsc, settings)

    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Skip per-product inserts after a batch save; otherwise delegate."""
        if self._lookup is not None:
            return None
        return self._inner.save_unknown_unspsc(unspsc)

    def get_default_unspsc(self, settings: CountrySettingsDTO) -> UnspscDataDTO:
        """Delegate default UNSPSC construction to the country Strategy."""
        return self._inner.get_default_unspsc(settings)

    def get_product_data(self, product_id: int) -> ProductDataDTO:
        """Return the prefetched product row or ask the country Strategy."""
        if self._lookup is not None:
            return self._lookup.products.get(product_id, EMPTY_PRODUCT_DATA)
        return self._inner.get_product_data(product_id)

    def resolve_tariff_data(self, partida: str | None) -> TariffDataDTO | None:
        """Return the prefetched tariff row or ask the country Strategy."""
        if not partida:
            return None
        if self._lookup is not None:
            return self._lookup.tariffs.get(partida)
        return self._inner.resolve_tariff_data(partida)

    def get_category_tree_courier(self, product_id: int) -> bool:
        """Return the prefetched category-tree flag or ask the country Strategy."""
        if self._lookup is not None:
            return self._lookup.category_courier.get(product_id, False)
        return self._inner.get_category_tree_courier(product_id)

    def load_products(self, product_ids: Sequence[int]) -> dict[int, ProductDataDTO]:
        """Delegate the batched product read to the country Strategy."""
        return self._inner.load_products(product_ids)

    def load_unspsc_map(
        self,
        codes: Sequence[str],
        settings: CountrySettingsDTO,
    ) -> dict[str, UnspscDataDTO | None]:
        """Delegate the batched UNSPSC read to the country Strategy."""
        return self._inner.load_unspsc_map(codes, settings)

    def save_unknown_unspsc_many(self, codes: Sequence[str]) -> None:
        """Delegate the batched unknown-UNSPSC insert to the country Strategy."""
        return self._inner.save_unknown_unspsc_many(codes)

    def load_tariffs(self, partidas: Sequence[str]) -> dict[str, TariffDataDTO]:
        """Delegate the batched tariff read to the country Strategy."""
        return self._inner.load_tariffs(partidas)

    def load_sales_iva_map(self, codes: Sequence[str]) -> dict[str, Decimal]:
        """Return prefetched sales-VAT rates or ask the country Strategy."""
        if self._lookup is not None:
            requested = tuple(dict.fromkeys(code for code in codes if code))
            if not requested:
                return {}
            return {
                code: rate
                for code, rate in self._lookup.sales_iva.items()
                if code in requested
            }
        return self._inner.load_sales_iva_map(codes)

    def resolve_sales_iva_rate(
        self,
        cabys: str | None,
        settings: CountrySettingsDTO,
        prefetched_rates: Mapping[str, Decimal] | None = None,
    ) -> SalesIvaDTO:
        """Delegate sales-VAT resolution, forwarding prefetched CABYS rates."""
        rates = prefetched_rates
        if rates is None and self._lookup is not None:
            rates = self._lookup.sales_iva
        return self._inner.resolve_sales_iva_rate(cabys, settings, rates)

    def load_category_tree_courier_map(
        self,
        product_ids: Sequence[int],
    ) -> dict[int, bool]:
        """Delegate the batched category-tree read to the country Strategy."""
        return self._inner.load_category_tree_courier_map(product_ids)

    def resolve_exchange_rate(self, settings: CountrySettingsDTO) -> Decimal:
        """Resolve the exchange rate once per request."""
        if self._exchange_rate is None:
            self._exchange_rate = self._inner.resolve_exchange_rate(settings)
        return self._exchange_rate

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Delegate the country calculator."""
        return self._inner.calculate(calculation)

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> DeliveryPromiseDTO:
        """Delegate the country delivery promise."""
        return self._inner.resolve_delivery_promise(offer, courier, settings)
