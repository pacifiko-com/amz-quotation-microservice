"""Direct Amazon-USD quotation flow for every country."""

from __future__ import annotations

from decimal import Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from DTO.quotation_context_dto import CountrySettingsDTO
from DTO.quotation_request_dto import (
    PriceQuotationProductDTO,
    PriceQuotationRequestDTO,
)
from DTO.quotation_response_dto import QuotationResponseDTO, ResultObjectDTO
from service.quotation_orchestrator import QuotationOrchestrator


class PriceQuotationService:
    """Quote products from a known Amazon USD price, without offer selection."""

    def __init__(self, orchestrator: QuotationOrchestrator | None = None) -> None:
        """Create the direct-price flow.

        Args:
            orchestrator: Shared policy, prefetch and product-loop runner.
        """
        self._orchestrator = orchestrator or QuotationOrchestrator()

    def quote(self, request: PriceQuotationRequestDTO) -> QuotationResponseDTO:
        """Quote all products from the provided Amazon USD amounts.

        Args:
            request: Validated country and products with ``amazon_price_usd``.

        Returns:
            QuotationResponseDTO: General status and one result per product.
                Product failures do not stop subsequent products.
        """
        return self._orchestrator.execute(
            request.country,
            request.products,
            self._quote_product,
        )

    def _quote_product(
        self,
        strategy: CountryQuotationStrategy,
        product: PriceQuotationProductDTO,
        settings: CountrySettingsDTO,
    ) -> ResultObjectDTO:
        """Resolve policy and calculate prices from the given Amazon USD amount.

        Args:
            strategy: Granular country decisions and data access.
            product: Amazon/Pacifiko facts and Amazon USD price.
            settings: Country constants loaded from ``oc_setting``.

        Returns:
            ResultObjectDTO: Prices and resolved trade policy, without an
                offer identifier or delivery promise.
        """
        policy = self._orchestrator.resolve_policy(strategy, product, settings)
        # 6. Se corta aquí para no ejecutar cálculo ni selección de oferta
        # sobre un producto que igual se va a rechazar.
        failed = self._orchestrator.validate_policy(
            product.product_id,
            policy,
            settings,
        )
        if failed is not None:
            return failed

        exchange_rate = strategy.resolve_exchange_rate(settings)
        calculation = self._orchestrator.calculate(
            strategy,
            product.amazon_price_usd,
            policy,
            exchange_rate,
            settings,
        )
        return self._orchestrator.priced_success(
            product.product_id,
            policy,
            settings,
            exchange_rate,
            product.amazon_price_usd,
            product.amazon_price_usd,
            calculation.cost_usd,
            calculation.price_local,
            special_price_dolar=Decimal("0"),
            special_price_local=Decimal("0"),
        )
