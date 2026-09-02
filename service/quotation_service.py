"""Common Guatemala-based quotation flow for every country."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from country.country_strategy_factory import CountryStrategyFactory
from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CountrySettingsDTO,
    EMPTY_PRODUCT_DATA,
    ProductDataDTO,
    QuotationLookupDTO,
    ResolvedPolicyDTO,
    SelectedOfferDTO,
    TariffDataDTO,
    UnspscDataDTO,
)
from DTO.quotation_request_dto import ProductQuotationDTO, QuotationRequestDTO
from DTO.quotation_response_dto import QuotationResponseDTO, ResultObjectDTO
from service.offer_selector import OfferSelector
from Utils.exceptions import QuotationError
from Utils.logger import get_logger

logger = get_logger(__name__)


class QuotationService:
    """Run one shared flow and delegate only variable decisions to Strategy."""

    def __init__(self, offer_selector: OfferSelector | None = None) -> None:
        """Create the common flow.

        Args:
            offer_selector: Shared GT-based raw Amazon offer selector.
        """
        self._offer_selector = offer_selector or OfferSelector()

    def quote(self, request: QuotationRequestDTO) -> QuotationResponseDTO:
        """Quote all products through one common orchestration.

        Args:
            request: Validated country and products with Amazon OFFERS.

        Returns:
            QuotationResponseDTO: General status and one result per product.
                Product failures do not stop subsequent products.
        """
        # El país solo se resuelve aquí: de este punto en adelante el flujo es
        # idéntico y las diferencias se piden a la Strategy.
        strategy = CountryStrategyFactory.create(request.country)

        # Obtener los settings según país. Se cargan una sola vez por request y
        # no por producto, para no repetir la consulta a oc_setting.
        try:
            settings = strategy.resolve_settings()
        except (QuotationError, ValueError) as exc:
            # Sin constantes no se puede cotizar nada: falla todo el request.
            return QuotationResponseDTO(
                success=False,
                message=str(exc),
                result=(),
            )

        if hasattr(strategy, "load_products"):
            strategy = _PrefetchedStrategy(
                strategy,
                self._prefetch(strategy, request.products, settings),
            )

        results = tuple(
            self._quote_safely(
                strategy,
                product,
                settings,
                request.prefer_amazon_fulfillment,
            )
            for product in request.products
        )

        # El estado general es exitoso solo si todos los productos se cotizaron.
        success_count = sum(item.success for item in results)
        if success_count == len(results):
            message = "All products were quoted successfully."
        elif success_count:
            message = "Quotation completed with partial failures."
        else:
            message = "No products could be quoted."
        return QuotationResponseDTO(
            success=success_count == len(results),
            message=message,
            result=results,
        )

    def _prefetch(
        self,
        strategy: CountryQuotationStrategy,
        products: tuple[ProductQuotationDTO, ...],
        settings: CountrySettingsDTO,
    ) -> QuotationLookupDTO:
        """Load every MySQL row needed by the request in a few batched queries.

        Args:
            strategy: Country provider selected once for the request.
            products: Products in request order.
            settings: Cached country constants.

        Returns:
            QuotationLookupDTO: In-memory maps reused by every product.
        """
        products_map = strategy.load_products(
            tuple(item.product_id for item in products)
        )
        unspsc_map = strategy.load_unspsc_map(
            tuple(item.unspsc for item in products),
            settings,
        )
        missing_codes = tuple(
            code for code, data in unspsc_map.items() if data is None
        )
        if missing_codes:
            strategy.save_unknown_unspsc_many(missing_codes)

        partidas = []
        for item in products:
            stored = products_map.get(item.product_id, EMPTY_PRODUCT_DATA)
            partida = item.pac_product_partida or stored.partida
            if partida:
                partidas.append(partida)
        tariffs = strategy.load_tariffs(partidas)

        need_category: list[int] = []
        for item in products:
            stored = products_map.get(item.product_id, EMPTY_PRODUCT_DATA)
            unspsc_data = unspsc_map.get(item.unspsc) or strategy.get_default_unspsc(
                settings
            )
            partida = item.pac_product_partida or stored.partida
            tariff = tariffs.get(partida) if partida else None
            if self._needs_category_tree(item, stored, unspsc_data, tariff):
                need_category.append(item.product_id)

        return QuotationLookupDTO(
            products=products_map,
            unspsc=unspsc_map,
            tariffs=tariffs,
            category_courier=(
                strategy.load_category_tree_courier_map(need_category)
                if need_category
                else {}
            ),
        )

    @staticmethod
    def _needs_category_tree(
        product: ProductQuotationDTO,
        product_data: ProductDataDTO,
        unspsc_data: UnspscDataDTO,
        tariff: TariffDataDTO | None,
    ) -> bool:
        """Return whether the category-tree courier query is still required.

        Args:
            product: Current product DTO.
            product_data: Stored Pacifiko product row.
            unspsc_data: Resolved or default UNSPSC policy.
            tariff: Assigned tariff row when a partida exists.

        Returns:
            bool: True only when no tariff is assigned and courier is still off.
        """
        if tariff is not None:
            return False
        product_courier = (
            product.pac_product_courier
            if product.pac_product_courier is not None
            else product_data.courier
        )
        courier = product_courier if product_courier else unspsc_data.courier
        return not courier

    def _quote_safely(
        self,
        strategy: CountryQuotationStrategy,
        product: ProductQuotationDTO,
        settings: CountrySettingsDTO,
        prefer_amazon_fulfillment: bool,
    ) -> ResultObjectDTO:
        """Protect the product loop from one controlled or unexpected failure.

        Args:
            strategy: Country provider selected once for the request.
            product: Current product DTO.
            settings: Country settings loaded once for the request.
            prefer_amazon_fulfillment: Request flag for the AF-first pass.

        Returns:
            ResultObjectDTO: Successful quote or isolated product failure.
        """
        # Aísla la falla de un producto para que los demás sí se coticen. Los
        # errores esperados e inesperados devuelven su mensaje al caller.
        try:
            return self._quote_product(
                strategy,
                product,
                settings,
                prefer_amazon_fulfillment,
            )
        except (QuotationError, ValueError) as exc:
            logger.warning(
                "Product quotation rejected product_id=%s: %s",
                product.product_id,
                exc,
            )
            return ResultObjectDTO(
                success=False,
                message=str(exc),
                product_id=product.product_id,
            )
        except Exception as exc:
            logger.exception("Product quotation failed product_id=%s", product.product_id)
            return ResultObjectDTO(
                success=False,
                message=_unexpected_product_error_message(exc),
                product_id=product.product_id,
            )

    def _quote_product(
        self,
        strategy: CountryQuotationStrategy,
        product: ProductQuotationDTO,
        settings: CountrySettingsDTO,
        prefer_amazon_fulfillment: bool = False,
    ) -> ResultObjectDTO:
        """Execute the shared quotation flow for one product.

        Args:
            strategy: Granular country decisions and data access.
            product: Amazon/Pacifiko facts for the product.
            settings: Country constants loaded from ``oc_setting``.
            prefer_amazon_fulfillment: Request flag for the AF-first pass.

        Returns:
            ResultObjectDTO: Prices, selected offer, promise and resolved
                trade policy.
        """
        # 1. Datos base: lo guardado en oc_product y la política del UNSPSC.
        product_data = strategy.get_product_data(product.product_id)
        unspsc_data = strategy.get_unspsc_data(product.unspsc, settings)

        # Si no se encuentra el UNSPSC, se guarda y se obtiene el default
        if unspsc_data is None:
            strategy.save_unknown_unspsc(product.unspsc)
            unspsc_data = strategy.get_default_unspsc(settings)

        # 2. La partida del request manda sobre la almacenada en oc_product.
        partida = product.pac_product_partida or product_data.partida
        tariff_partida_data = strategy.resolve_tariff_data(partida)

        # 3. Peso: el override del request reemplaza al de oc_product, y sobre
        # ese resultado se toma el mayor contra el peso de Amazon.
        stored_weight = (
            product.pac_product_weight
            if product.pac_product_weight is not None
            else product_data.weight_kg
        )
        KG_TO_LB = 2.20462
        weight = max(
            product.amz_weight_kg,
            stored_weight / Decimal(KG_TO_LB) if stored_weight is not None else product.amz_weight_kg,
        )

        # 4. Courier: se evalúa en cascada y una vez encendido ya no se apaga,
        # por eso cada fuente solo puede activarlo si la anterior no lo hizo.
        product_courier = (
            product.pac_product_courier
            if product.pac_product_courier is not None
            else product_data.courier
        )
        courier = product_courier if product_courier else unspsc_data.courier
        arancel = unspsc_data.arancel_percentage
        restriction = unspsc_data.restriction

        # 5. Precedencia final de la política de importación:
        # con partida asignada, esa partida es la fuente autoritativa y
        # reemplaza al UNSPSC, pero solo en modo no courier
        if tariff_partida_data is not None:
            courier = tariff_partida_data.courier if tariff_partida_data.courier is not None else courier
            if not courier:
                if tariff_partida_data.arancel_percentage is not None:
                    arancel = tariff_partida_data.arancel_percentage
                if tariff_partida_data.restriction is not None:
                    restriction = tariff_partida_data.restriction
        elif not courier:
            # Sin partida, el árbol de categorías es el último recurso para
            # determinar si el producto debe ir por courier.
            courier = strategy.get_category_tree_courier(product.product_id)

        policy = ResolvedPolicyDTO(
            weight_kg=weight,
            arancel_percentage=arancel,
            margin_percentage=(
                unspsc_data.margin_percentage
                if unspsc_data.margin_percentage > 0
                else settings.decimal("margen")
            ),
            courier=bool(courier),
            restriction=restriction,
            danger_good_active=unspsc_data.danger_good_active,
            partida=tariff_partida_data.partida if tariff_partida_data else partida,
        )
        # 6. Se corta aquí para no ejecutar cálculo ni selección de oferta
        # sobre un producto que igual se va a rechazar.
        failed = self._validate_policy(product.product_id, policy, settings)
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
            )

        exchange_rate = strategy.resolve_exchange_rate(settings)
        (
            price_dolar,
            special_dolar,
            cost_dolar,
            price_local,
            special_price_local,
        ) = self._resolve_prices(
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
        return ResultObjectDTO(
            success=True,
            message="Product quoted successfully.",
            product_id=product.product_id,
            offer_id=offer.offer_id,
            amazon_price=offer.price_usd,
            price_dolar=price_dolar,
            cost_dolar=cost_dolar,
            cost_local=cost_dolar * exchange_rate,
            special_price_dolar=special_dolar,
            price_local=price_local,
            special_price_local=special_price_local,
            exchange_rate=exchange_rate,
            currency_code=settings.require("currency_code"),
            delivery_promise_amz=promise,
            courier=policy.courier,
            restriction=policy.restriction,
            partida=policy.partida,
        )

    @staticmethod
    def _validate_policy(
        product_id: int,
        policy: ResolvedPolicyDTO,
        settings: CountrySettingsDTO,
    ) -> ResultObjectDTO | None:
        """Apply shared restriction and weight gates.

        Args:
            product_id: Pacifiko product identifier.
            policy: Resolved common product policy.
            settings: Configured common weight limit.

        Returns:
            ResultObjectDTO | None: Failure when a gate rejects the product;
                otherwise ``None``.
        """
        # Las condiciones se evalúan en cascada y se devuelve solo el primer
        # motivo. El resultado incluye la política ya resuelta para que el
        # caller vea con qué datos se tomó la decisión.
        if policy.restriction:
            message = "Product is restricted."
        elif policy.weight_kg <= 0:
            message = "Product weight is required."
        elif policy.weight_kg >= settings.decimal("max_product_weight_kg"):
            message = "Product exceeds the configured weight limit."
        else:
            return None
        return ResultObjectDTO(
            success=False,
            message=message,
            product_id=product_id,
            courier=policy.courier,
            restriction=policy.restriction,
            partida=policy.partida,
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
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """Calculate offer and list prices, then decide if a special applies.

        Args:
            strategy: Country calculator used for both USD bases.
            offer: Selected Amazon offer and optional list price.
            policy: Resolved common product policy.
            exchange_rate: Country exchange rate for this request.
            settings: Configured special discount threshold.

        Returns:
            tuple[Decimal, Decimal, Decimal, Decimal, Decimal]: Public USD
                price, special USD price, landed cost USD, public local
                price and special local price.
        """
        offer_calc = strategy.calculate(
            CalculationInputDTO(
                amazon_price_usd=offer.price_usd,
                policy=policy,
                exchange_rate=exchange_rate,
                settings=settings,
            )
        )

        # Sin precio de lista no hay segunda corrida: un solo cálculo.
        if offer.list_price_usd is None or offer.list_price_usd <= 0:
            return (
                offer.price_usd,
                Decimal("0"),
                offer_calc.cost_usd,
                offer_calc.price_local,
                Decimal("0"),
            )

        # El umbral se compara sobre los precios locales ya calculados, no
        # sobre los montos USD. El costo siempre sale de la oferta.
        list_calc = strategy.calculate(
            CalculationInputDTO(
                amazon_price_usd=offer.list_price_usd,
                policy=policy,
                exchange_rate=exchange_rate,
                settings=settings,
            )
        )
        if list_calc.price_local <= 0:
            return (
                offer.price_usd,
                Decimal("0"),
                offer_calc.cost_usd,
                offer_calc.price_local,
                Decimal("0"),
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
            return (
                offer.price_usd,
                Decimal("0"),
                offer_calc.cost_usd,
                offer_calc.price_local,
                Decimal("0"),
            )
        return (
            offer.list_price_usd,
            offer.price_usd,
            offer_calc.cost_usd,
            list_calc.price_local,
            offer_calc.price_local,
        )


class _PrefetchedStrategy:
    """Serve batched MySQL rows while keeping calculator calls on the Strategy."""

    def __init__(
        self,
        inner: CountryQuotationStrategy,
        lookup: QuotationLookupDTO,
    ) -> None:
        """Create a read-through wrapper for one request.

        Args:
            inner: Country Strategy that owns formulas and exchange rate.
            lookup: Rows loaded before the product loop.
        """
        self._inner = inner
        self._lookup = lookup
        self._exchange_rate = None

    def resolve_settings(self) -> CountrySettingsDTO:
        """Return settings already resolved by the common flow."""
        return self._inner.resolve_settings()

    def get_unspsc_data(
        self,
        unspsc: str,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO | None:
        """Return the prefetched UNSPSC row."""
        if unspsc in self._lookup.unspsc:
            return self._lookup.unspsc[unspsc]
        return self._inner.get_unspsc_data(unspsc, settings)

    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Skip per-product inserts; unknown codes were saved in the batch."""
        return None

    def get_default_unspsc(self, settings: CountrySettingsDTO) -> UnspscDataDTO:
        """Build defaults for an unknown UNSPSC without a database round-trip."""
        return self._inner.get_default_unspsc(settings)

    def get_product_data(self, product_id: int) -> ProductDataDTO:
        """Return the prefetched product row or an empty DTO."""
        return self._lookup.products.get(product_id, EMPTY_PRODUCT_DATA)

    def resolve_tariff_data(self, partida: str | None) -> TariffDataDTO | None:
        """Return the prefetched tariff row when the code exists."""
        if not partida:
            return None
        if partida in self._lookup.tariffs:
            return self._lookup.tariffs[partida]
        return self._inner.resolve_tariff_data(partida)

    def get_category_tree_courier(self, product_id: int) -> bool:
        """Return the prefetched category-tree courier flag."""
        return self._lookup.category_courier.get(product_id, False)

    def resolve_exchange_rate(self, settings: CountrySettingsDTO):
        """Resolve the exchange rate once per request."""
        if self._exchange_rate is None:
            self._exchange_rate = self._inner.resolve_exchange_rate(settings)
        return self._exchange_rate

    def calculate(self, calculation: CalculationInputDTO):
        """Delegate the country calculator."""
        return self._inner.calculate(calculation)

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> int:
        """Delegate the country delivery promise."""
        return self._inner.resolve_delivery_promise(offer, courier, settings)


def _unexpected_product_error_message(exc: BaseException) -> str:
    """Build a product message that includes the unexpected error.

    Args:
        exc: Exception that escaped the product quotation flow.

    Returns:
        str: Generic prefix plus exception type and detail.
    """
    detail = str(exc).strip()
    name = type(exc).__name__
    if detail:
        return f"Unexpected product quotation failure: {name}: {detail}"
    return f"Unexpected product quotation failure: {name}"

