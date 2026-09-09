"""Offer-selection quotation flow for every country."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from DTO.quotation_context_dto import (
    CountrySettingsDTO,
    ResolvedPolicyDTO,
    ResolvedPricesDTO,
    SelectedOfferDTO,
)
from DTO.quotation_request_dto import ProductQuotationDTO, QuotationRequestDTO
from DTO.quotation_response_dto import QuotationResponseDTO, ResultObjectDTO
from service.offer_selector import OfferSelector
from service.quotation_orchestrator import QuotationOrchestrator


class QuotationService:
    """Quote products after selecting and validating an Amazon offer."""

    def __init__(
        self,
        offer_selector: OfferSelector | None = None,
        orchestrator: QuotationOrchestrator | None = None,
    ) -> None:
        """Create the offer-selection flow.

        Args:
            offer_selector: Shared GT-based raw Amazon offer selector.
            orchestrator: Shared policy, prefetch and product-loop runner.
        """
        self._offer_selector = offer_selector or OfferSelector()
        self._orchestrator = orchestrator or QuotationOrchestrator()

    def quote(self, request: QuotationRequestDTO) -> QuotationResponseDTO:
        """Quote all products through offer selection and country calculators.

        Args:
            request: Validated country and products with Amazon OFFERS.

        Returns:
            QuotationResponseDTO: General status and one result per product.
                Product failures do not stop subsequent products.
        """
        return self._orchestrator.execute(
            request.country,
            request.products,
            lambda strategy, product, settings: self._quote_product(
                strategy,
                product,
                settings,
                request.prefer_amazon_fulfillment,
            ),
        )

    def _quote_product(
        self,
        strategy: CountryQuotationStrategy,
        product: ProductQuotationDTO,
        settings: CountrySettingsDTO,
        prefer_amazon_fulfillment: bool = False,
    ) -> ResultObjectDTO:
        """Resolve policy, select an offer and calculate prices for one product.

        Args:
            strategy: Granular country decisions and data access.
            product: Amazon/Pacifiko facts and raw offers.
            settings: Country constants loaded from ``oc_setting``.
            prefer_amazon_fulfillment: Request flag for the AF-first pass.

        Returns:
            ResultObjectDTO: Prices, selected offer, promise and resolved
                trade policy.
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

        # 7. Oferta, tasa y cálculo. La oferta se elige con reglas compartidas;
        # la fórmula la aplica cada país sobre el mismo DTO de entrada.
        offer = self._offer_selector.select(
            product.amz_offers,
            settings,
            prefer_amazon_fulfillment,
        )
        if self._is_restricted_offer(offer, settings):
            return ResultObjectDTO(
                success=False,
                message="Amazon offer is restricted.",
                product_id=product.product_id,
                offer_id=offer.offer_id,
                courier=policy.courier,
                restriction=policy.restriction,
                partida=policy.partida,
                cabys=policy.cabys,
            )

        exchange_rate = strategy.resolve_exchange_rate(settings)
        prices = self._resolve_prices(
            strategy,
            offer,
            policy,
            exchange_rate,
            settings,
        )
        promise = strategy.resolve_delivery_promise(
            offer,
            policy.courier,
            settings,
        )
        return self._orchestrator.priced_success(
            product.product_id,
            policy,
            settings,
            exchange_rate,
            prices.amazon_price,
            prices.cost_usd,
            prices.price_usd,
            prices.price_local,
            prices.price_without_tax_usd,
            prices.price_without_tax_local,
            special_amazon_price=prices.special_amazon_price,
            special_price_dolar=prices.special_price_usd,
            special_price_local=prices.special_price_local,
            special_price_without_tax_dolar=prices.special_price_without_tax_usd,
            special_price_without_tax_local=prices.special_price_without_tax_local,
            offer_id=offer.offer_id,
            delivery_promise_amz=promise,
        )

    @staticmethod
    def _is_restricted_offer(
        offer: SelectedOfferDTO,
        settings: CountrySettingsDTO,
    ) -> bool:
        """Detect restricted buying guidance on the already selected offer.

        Args:
            offer: Offer chosen by the shared selector.
            settings: Term used for the contains check.

        Returns:
            bool: True when the selected offer must abort the product.
        """
        # Solo se revisa si buyingGuidance trae texto. El contains se aplica
        # sobre la oferta ya elegida: no se busca otra oferta de reemplazo.
        if not offer.buying_guidance.strip():
            return False
        term = settings.require("restricted_guidance_term").casefold()
        return any(
            term in value.casefold()
            for value in (
                offer.buying_guidance,
                offer.buying_guidance_title,
                offer.buying_guidance_type,
            )
        )

    @staticmethod
    def _resolve_prices(
        strategy: CountryQuotationStrategy,
        offer: SelectedOfferDTO,
        policy: ResolvedPolicyDTO,
        exchange_rate: Decimal,
        settings: CountrySettingsDTO,
    ) -> ResolvedPricesDTO:
        """Calculate offer and list prices, then decide if a special applies.

        Args:
            strategy: Country calculator used for both USD bases.
            offer: Selected Amazon offer and optional list price.
            policy: Resolved common product policy.
            exchange_rate: Country exchange rate for this request.
            settings: Configured special discount threshold.

        Returns:
            ResolvedPricesDTO: Amazon amounts and quoted sale prices.
        """
        offer_calc = QuotationOrchestrator.calculate(
            strategy,
            offer.price_usd,
            policy,
            exchange_rate,
            settings,
        )
        # Sin precio de lista no hay segunda corrida: un solo cálculo.
        if offer.list_price_usd is None or offer.list_price_usd <= 0:
            return ResolvedPricesDTO(
                amazon_price=offer.price_usd,
                special_amazon_price=None,
                cost_usd=offer_calc.cost_usd,
                price_usd=offer_calc.price_usd,
                price_local=offer_calc.price_local,
                price_without_tax_usd=offer_calc.price_without_tax_usd,
                price_without_tax_local=offer_calc.price_without_tax_local,
                special_price_usd=None,
                special_price_local=None,
                special_price_without_tax_usd=None,
                special_price_without_tax_local=None,
            )

        # El umbral se compara sobre los precios locales ya calculados, no
        # sobre los montos USD. El costo siempre sale de la oferta.
        list_calc = QuotationOrchestrator.calculate(
            strategy,
            offer.list_price_usd,
            policy,
            exchange_rate,
            settings,
        )
        if list_calc.price_local <= 0:
            return ResolvedPricesDTO(
                amazon_price=offer.price_usd,
                special_amazon_price=None,
                cost_usd=offer_calc.cost_usd,
                price_usd=offer_calc.price_usd,
                price_local=offer_calc.price_local,
                price_without_tax_usd=offer_calc.price_without_tax_usd,
                price_without_tax_local=offer_calc.price_without_tax_local,
                special_price_usd=None,
                special_price_local=None,
                special_price_without_tax_usd=None,
                special_price_without_tax_local=None,
            )

        discount_pct = (
            (list_calc.price_local - offer_calc.price_local)
            / list_calc.price_local
            * Decimal("100")
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        threshold_pct = (
            settings.decimal("special_discount_threshold") * Decimal("100")
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if discount_pct < threshold_pct:
            return ResolvedPricesDTO(
                amazon_price=offer.price_usd,
                special_amazon_price=None,
                cost_usd=offer_calc.cost_usd,
                price_usd=offer_calc.price_usd,
                price_local=offer_calc.price_local,
                price_without_tax_usd=offer_calc.price_without_tax_usd,
                price_without_tax_local=offer_calc.price_without_tax_local,
                special_price_usd=None,
                special_price_local=None,
                special_price_without_tax_usd=None,
                special_price_without_tax_local=None,
            )
        return ResolvedPricesDTO(
            amazon_price=offer.list_price_usd,
            special_amazon_price=offer.price_usd,
            cost_usd=offer_calc.cost_usd,
            price_usd=list_calc.price_usd,
            price_local=list_calc.price_local,
            price_without_tax_usd=list_calc.price_without_tax_usd,
            price_without_tax_local=list_calc.price_without_tax_local,
            special_price_usd=offer_calc.price_usd,
            special_price_local=offer_calc.price_local,
            special_price_without_tax_usd=offer_calc.price_without_tax_usd,
            special_price_without_tax_local=offer_calc.price_without_tax_local,
        )
