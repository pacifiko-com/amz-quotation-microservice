"""Shared quotation orchestration reused by offer and direct-price flows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from country.country_strategy_factory import CountryStrategyFactory
from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    EMPTY_PRODUCT_DATA,
    ProductDataDTO,
    QuotationLookupDTO,
    ResolvedPolicyDTO,
    TariffDataDTO,
    UnspscDataDTO,
)
from DTO.quotation_request_dto import ProductFactsDTO
from DTO.quotation_response_dto import QuotationResponseDTO, ResultObjectDTO
from Utils.exceptions import QuotationError
from Utils.logger import get_logger

logger = get_logger(__name__)

QuoteProductFn = Callable[
    [CountryQuotationStrategy, ProductFactsDTO, CountrySettingsDTO],
    ResultObjectDTO,
]

_KG_TO_LB = Decimal("2.20462")


class QuotationOrchestrator:
    """Run settings, prefetch and the per-product loop for any quote step."""

    def execute(
        self,
        country: str,
        products: Sequence[ProductFactsDTO],
        quote_product: QuoteProductFn,
    ) -> QuotationResponseDTO:
        """Resolve country collaborators and quote every product safely.

        Args:
            country: Requested country code.
            products: Products in request order.
            quote_product: Flow-specific step after shared setup.

        Returns:
            QuotationResponseDTO: Envelope with one result per product.
        """
        # El país solo se resuelve aquí: de este punto en adelante el flujo es
        # idéntico y las diferencias se piden a la Strategy.
        strategy = CountryStrategyFactory.create(country)

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

        # Fetch data in a single batch query and bind it to the strategy
        # to reuse it by every product quotation.
        bind_lookup = getattr(strategy, "bind_lookup", None)
        if callable(bind_lookup):
            bind_lookup(self.prefetch(strategy, products, settings))

        results = tuple(
            self.quote_safely(strategy, product, settings, quote_product)
            for product in products
        )
        return _response_from_results(results)

    def prefetch(
        self,
        strategy: CountryQuotationStrategy,
        products: Sequence[ProductFactsDTO],
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
        # None unspsc requires using the default policy. Avoid saving it in unknown codes table.
        unspsc_map = strategy.load_unspsc_map(
            tuple(item.unspsc for item in products if item.unspsc is not None),
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
        cabys_codes = tuple(
            stored.cabys
            for stored in products_map.values()
            if stored.cabys
        )

        need_category: list[int] = []
        for item in products:
            stored = products_map.get(item.product_id, EMPTY_PRODUCT_DATA)
            unspsc_data = (
                strategy.get_default_unspsc(settings)
                if item.unspsc is None
                else unspsc_map.get(item.unspsc) or strategy.get_default_unspsc(
                    settings
                )
            )
            partida = item.pac_product_partida or stored.partida
            tariff = tariffs.get(partida) if partida else None
            if self.needs_category_tree(item, stored, unspsc_data, tariff):
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
            sales_iva=strategy.load_sales_iva_map(cabys_codes),
        )

    @staticmethod
    def needs_category_tree(
        product: ProductFactsDTO,
        product_data: ProductDataDTO,
        unspsc_data: UnspscDataDTO,
        tariff: TariffDataDTO | None,
    ) -> bool:
        """Return whether the category-tree courier query is still required.

        Args:
            product: Current product facts.
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

    def quote_safely(
        self,
        strategy: CountryQuotationStrategy,
        product: ProductFactsDTO,
        settings: CountrySettingsDTO,
        quote_product: QuoteProductFn,
    ) -> ResultObjectDTO:
        """Protect the product loop from one controlled or unexpected failure.

        Args:
            strategy: Country provider selected once for the request.
            product: Current product facts.
            settings: Country settings loaded once for the request.
            quote_product: Flow-specific quotation step.

        Returns:
            ResultObjectDTO: Successful quote or isolated product failure.
        """
        # Aísla la falla de un producto para que los demás sí se coticen. Los
        # errores esperados e inesperados devuelven su mensaje al caller.
        try:
            return quote_product(strategy, product, settings)
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

    def resolve_policy(
        self,
        strategy: CountryQuotationStrategy,
        product: ProductFactsDTO,
        settings: CountrySettingsDTO,
    ) -> ResolvedPolicyDTO:
        """Resolve weight, partida, courier, tariff and UNSPSC policy.

        Args:
            strategy: Granular country decisions and data access.
            product: Amazon/Pacifiko facts for the product.
            settings: Country constants loaded from ``oc_setting``.

        Returns:
            ResolvedPolicyDTO: Combined import policy for the product.
        """
        # 1. Datos base: lo guardado en oc_product y la política del UNSPSC.
        product_data = strategy.get_product_data(product.product_id)
        if product.unspsc is None:
            unspsc_data = strategy.get_default_unspsc(settings)
        else:
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
        weight = max(
            product.amz_weight_kg,
            stored_weight / _KG_TO_LB
            if stored_weight is not None
            else product.amz_weight_kg,
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
            courier = (
                tariff_partida_data.courier
                if tariff_partida_data.courier is not None
                else courier
            )
            if not courier:
                if tariff_partida_data.arancel_percentage is not None:
                    arancel = tariff_partida_data.arancel_percentage
                if tariff_partida_data.restriction is not None:
                    restriction = tariff_partida_data.restriction
        elif not courier:
            # Sin partida, el árbol de categorías es el último recurso para
            # determinar si el producto debe ir por courier.
            courier = strategy.get_category_tree_courier(product.product_id)

        cabys = product_data.cabys
        return ResolvedPolicyDTO(
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
            sales_iva_rate=strategy.resolve_sales_iva_rate(cabys, settings),
            cabys=cabys,
        )

    @staticmethod
    def validate_policy(
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
            cabys=policy.cabys,
        )

    @staticmethod
    def calculate(
        strategy: CountryQuotationStrategy,
        amazon_price_usd: Decimal,
        policy: ResolvedPolicyDTO,
        exchange_rate: Decimal,
        settings: CountrySettingsDTO,
    ) -> CalculationResultDTO:
        """Run the country calculator for one Amazon USD amount.

        Args:
            strategy: Country calculator.
            amazon_price_usd: USD amount used as calculator input.
            policy: Resolved common product policy.
            exchange_rate: Country exchange rate for this request.
            settings: Country constants loaded from ``oc_setting``.

        Returns:
            CalculationResultDTO: Landed cost USD, sale price USD and local
                sale price.
        """
        return strategy.calculate(
            CalculationInputDTO(
                amazon_price_usd=amazon_price_usd,
                policy=policy,
                exchange_rate=exchange_rate,
                settings=settings,
            )
        )

    @staticmethod
    def priced_success(
        product_id: int,
        policy: ResolvedPolicyDTO,
        settings: CountrySettingsDTO,
        exchange_rate: Decimal,
        amazon_price: Decimal,
        cost_dolar: Decimal,
        price_dolar: Decimal,
        price_local: Decimal,
        special_amazon_price: Decimal | None = None,
        special_price_dolar: Decimal | None = None,
        special_price_local: Decimal | None = None,
        offer_id: str = "",
        delivery_promise_amz: int | None = None,
    ) -> ResultObjectDTO:
        """Build a successful priced result with the shared response fields.

        Args:
            product_id: Pacifiko product identifier.
            policy: Resolved import policy.
            settings: Country constants that include the currency code.
            exchange_rate: USD-to-local rate used for this product.
            amazon_price: Public Amazon USD amount.
            cost_dolar: Landed cost in USD.
            price_dolar: Quoted sale price in USD from the calculator.
            price_local: Quoted sale price in local currency.
            special_amazon_price: Special Amazon USD price, or ``None``.
            special_price_dolar: Quoted special sale price in USD, or ``None``.
            special_price_local: Quoted special local price, or ``None``.
            offer_id: Selected Amazon offer identifier when applicable.
            delivery_promise_amz: Country promise tier when an offer exists.

        Returns:
            ResultObjectDTO: Successful product quotation.
        """
        return ResultObjectDTO(
            success=True,
            message="Product quoted successfully.",
            product_id=product_id,
            offer_id=offer_id,
            amazon_price=amazon_price,
            special_amazon_price=special_amazon_price,
            price_dolar=price_dolar,
            price_local=price_local,
            special_price_dolar=special_price_dolar,
            special_price_local=special_price_local,
            cost_dolar=cost_dolar,
            cost_local=cost_dolar * exchange_rate,
            exchange_rate=exchange_rate,
            currency_code=settings.require("currency_code"),
            delivery_promise_amz=delivery_promise_amz,
            courier=policy.courier,
            restriction=policy.restriction,
            partida=policy.partida,
            cabys=policy.cabys,
        )


def _response_from_results(
    results: tuple[ResultObjectDTO, ...],
) -> QuotationResponseDTO:
    """Build the request envelope from per-product results.

    Args:
        results: One result per input product, in the original order.

    Returns:
        QuotationResponseDTO: General status and product results.
    """
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
