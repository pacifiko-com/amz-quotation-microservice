"""Shared quotation orchestration reused by offer and direct-price flows."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, ROUND_CEILING

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
from config.settings import get_settings, quote_worker_count
from const import KG_TO_LB
from DTO.quotation_request_dto import ProductFactsDTO
from DTO.quotation_response_dto import QuotationResponseDTO, ResultObjectDTO
from Utils.exceptions import QuotationError
from Utils.logger import get_logger

logger = get_logger(__name__)

QuoteProductFn = Callable[
    [CountryQuotationStrategy, ProductFactsDTO, CountrySettingsDTO],
    ResultObjectDTO,
]


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

        resolve_exchange_rate = getattr(strategy, "resolve_exchange_rate", None)
        if callable(resolve_exchange_rate):
            resolve_exchange_rate(settings)

        results = self._quote_products(
            strategy, products, settings, quote_product
        )
        return _response_from_results(results)

    def _quote_products(
        self,
        strategy: CountryQuotationStrategy,
        products: Sequence[ProductFactsDTO],
        settings: CountrySettingsDTO,
        quote_product: QuoteProductFn,
    ) -> tuple[ResultObjectDTO, ...]:
        """Quote products sequentially or in a bounded thread pool.

        Args:
            strategy: Country Strategy already bound to the prefetch map.
            products: Products in request order.
            settings: Country constants loaded once for the request.
            quote_product: Flow-specific quotation step.

        Returns:
            tuple[ResultObjectDTO, ...]: One result per input product.
        """
        app_settings = get_settings()
        workers = quote_worker_count(
            len(products),
            app_settings.quotation_worker_threads,
            app_settings.quotation_min_products_per_worker,
        )
        if workers <= 1:
            return tuple(
                self.quote_safely(strategy, product, settings, quote_product)
                for product in products
            )

        results: list[ResultObjectDTO | None] = [None] * len(products)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_indexes = {
                pool.submit(
                    self.quote_safely,
                    strategy,
                    product,
                    settings,
                    quote_product,
                ): index
                for index, product in enumerate(products)
            }
            for future in as_completed(future_indexes):
                results[future_indexes[future]] = future.result()
        return tuple(results)

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
            return QuotationOrchestrator.failed_result(
                product.product_id,
                str(exc),
                quotation_notes=getattr(exc, "quotation_notes", ()),
            )
        except Exception as exc:
            correlation_id = uuid.uuid4().hex
            logger.exception(
                "Product quotation failed product_id=%s correlation_id=%s",
                product.product_id,
                correlation_id,
                exc_info=exc,
            )
            return QuotationOrchestrator.failed_result(
                product.product_id,
                _unexpected_product_error_message(),
                quotation_notes=getattr(exc, "quotation_notes", ()),
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
        notes: list[str] = []
        product_data = strategy.get_product_data(product.product_id)
        if product.unspsc is None:
            unspsc_data = strategy.get_default_unspsc(settings)
            notes.append(
                "Producto no tiene UNSPSC; se usan valores por defecto de "
                f"oc_setting ({_unspsc_summary(unspsc_data)}). "
                "No se registra como desconocido."
            )
        else:
            unspsc_data = strategy.get_unspsc_data(product.unspsc, settings)
            # Si no se encuentra el UNSPSC, se guarda y se obtiene el default
            if unspsc_data is None:
                strategy.save_unknown_unspsc(product.unspsc)
                unspsc_data = strategy.get_default_unspsc(settings)
                notes.append(
                    f"UNSPSC {product.unspsc} no encontrado en oc_arancel_amz; "
                    "se almacena en desconocidos y se usan valores por defecto "
                    f"de oc_setting ({_unspsc_summary(unspsc_data)})."
                )
            else:
                notes.append(
                    f"UNSPSC {product.unspsc} encontrado en oc_arancel_amz "
                    f"({_unspsc_summary(unspsc_data)})."
                )

        # 2. La partida del request manda sobre la almacenada en oc_product.
        if product.pac_product_partida:
            partida = product.pac_product_partida
            notes.append(
                f"Partida arancelaria {partida} tomada del override del request."
            )
        elif product_data.partida:
            partida = product_data.partida
            notes.append(f"Partida arancelaria {partida} tomada de oc_product.")
        else:
            partida = None
            notes.append("Producto no tiene partida arancelaria asignada.")
        tariff_partida_data = strategy.resolve_tariff_data(partida)
        if tariff_partida_data is not None:
            notes.extend(tariff_partida_data.quotation_notes)

        # 3. Peso: el override del request reemplaza al de oc_product, y sobre
        # ese resultado se toma el mayor contra el peso de Amazon.
        if product.pac_product_weight is not None:
            stored_weight = product.pac_product_weight
            notes.append(
                f"Peso Pacifiko tomado del override del request: {stored_weight}."
            )
        elif product_data.weight_lb is not None:
            stored_weight = product_data.weight_lb
            notes.append(f"Peso Pacifiko tomado de oc_product: {stored_weight} lb.")
        else:
            stored_weight = None
            notes.append("No hay peso Pacifiko; se usa solo el peso Amazon.")
        
        amz_weight_lb = (product.amz_weight_kg * KG_TO_LB).to_integral_value(
            rounding=ROUND_CEILING
        )
        weight_lb = max(
            amz_weight_lb,
            stored_weight
            if stored_weight is not None
            else amz_weight_lb,
        )
        notes.append(f"Peso Amazon del request: {product.amz_weight_kg} kg convertido a {amz_weight_lb} lb.")
        
        notes.append(
            f"Peso usado: {weight_lb} lb (máximo entre Amazon (convertido a lb y redondeado hacia arriba) y Pacifiko (valor de oc_setting almacenado en lb))."
        )

        # 4. Courier: se evalúa en cascada y una vez encendido ya no se apaga,
        # por eso cada fuente solo puede activarlo si la anterior no lo hizo.
        if product.pac_product_courier is not None:
            product_courier = product.pac_product_courier
            notes.append(
                f"Courier de producto tomado del override del request: "
                f"{product_courier}."
            )
        else:
            product_courier = product_data.courier
            notes.append(
                f"Courier de producto tomado de oc_product: {product_courier}."
            )
        if product_courier:
            courier = product_courier
            notes.append(
                "Courier activado por producto; no se usa el courier UNSPSC."
            )
        else:
            courier = unspsc_data.courier
            notes.append(
                "Courier de producto no activo; se usa courier UNSPSC: "
                f"{courier}."
            )
        arancel = unspsc_data.arancel_percentage
        restriction = unspsc_data.restriction

        # 5. Precedencia final de la política de importación:
        # con partida asignada, esa partida es la fuente autoritativa y
        # reemplaza al UNSPSC, pero solo en modo no courier
        if tariff_partida_data is not None:
            notes.append(
                f"Partida {tariff_partida_data.partida} encontrada; "
                f"courier de partida: {tariff_partida_data.courier}."
            )
            if tariff_partida_data.courier is not None:
                courier = tariff_partida_data.courier
                notes.append(f"Courier tomado de la partida: {courier}.")
            else:
                notes.append(
                    "Partida sin courier; se conserva el courier ya resuelto."
                )
            if not courier:
                if tariff_partida_data.arancel_percentage is not None:
                    arancel = tariff_partida_data.arancel_percentage
                    notes.append(
                        "Producto no es courier; arancel tomado de la partida: "
                        f"{arancel}."
                    )
                else:
                    notes.append(
                        "Producto no es courier; partida sin arancel, se "
                        f"conserva el UNSPSC: {arancel}."
                    )
                if tariff_partida_data.restriction is not None:
                    restriction = tariff_partida_data.restriction
                    notes.append(
                        "Producto no es courier; restricción tomada de la "
                        f"partida: {restriction}."
                    )
                else:
                    notes.append(
                        "Producto no es courier; partida sin restricción, se "
                        f"conserva el UNSPSC: {restriction}."
                    )
            else:
                notes.append(
                    "Producto es courier; arancel y restricción se conservan "
                    f"del UNSPSC ({arancel}, restricción {restriction})."
                )
        elif not courier:
            # Sin partida, el árbol de categorías es el último recurso para
            # determinar si el producto debe ir por courier.
            courier = strategy.get_category_tree_courier(product.product_id)
            notes.append(
                "Sin partida y courier aún apagado; courier del árbol de "
                f"categorías: {courier}."
            )
        else:
            notes.append(
                "Sin partida; courier ya activo, no se consulta el árbol de "
                "categorías."
            )

        cabys = product_data.cabys
        if unspsc_data.margin_percentage > 0:
            margin = unspsc_data.margin_percentage
            notes.append(f"Margen {margin} tomado de UNSPSC (oc_arancel_amz).")
        else:
            margin = settings.decimal("margen")
            notes.append(
                f"Margen UNSPSC no usable; se usa oc_setting margen: {margin}."
            )
        notes.append(
            f"Mercancía peligrosa: {unspsc_data.danger_good_active} "
            "(UNSPSC/defaults)."
        )
        sales_iva_rate, iva_notes = _unpack_sales_iva(
            strategy.resolve_sales_iva_rate(cabys, settings)
        )
        notes.extend(iva_notes)
        return ResolvedPolicyDTO(
            weight_lb=weight_lb,
            arancel_percentage=arancel,
            margin_percentage=margin,
            courier=bool(courier),
            restriction=restriction,
            danger_good_active=unspsc_data.danger_good_active,
            partida=tariff_partida_data.partida if tariff_partida_data else partida,
            sales_iva_rate=sales_iva_rate,
            cabys=cabys,
            quotation_notes=tuple(notes),
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
        weight_kg = policy.weight_lb / KG_TO_LB

        if policy.restriction:
            message = "Product is restricted."
        elif policy.weight_lb <= 0:
            message = "Product weight is required."
        elif weight_kg >= settings.decimal("max_product_weight_kg"):
            message = "Product exceeds the configured weight limit (compared in KG)."
        else:
            return None
        return QuotationOrchestrator.failed_result(
            product_id,
            message,
            policy=policy,
        )

    @staticmethod
    def failed_result(
        product_id: int,
        message: str,
        *,
        policy: ResolvedPolicyDTO | None = None,
        offer_id: str = "",
        quotation_notes: Sequence[str] | None = None,
    ) -> ResultObjectDTO:
        """Build a failed product result, keeping any notes already collected.

        Args:
            product_id: Pacifiko product identifier.
            message: Product-level failure reason.
            policy: Resolved policy when the failure happens after it.
            offer_id: Selected Amazon offer identifier when applicable.
            quotation_notes: Decision trail up to the failure. Defaults to
                the policy notes when a policy is present.

        Returns:
            ResultObjectDTO: Isolated product failure.
        """
        if quotation_notes is not None:
            notes = tuple(quotation_notes)
        elif policy is not None:
            notes = policy.quotation_notes
        else:
            notes = ()
        return ResultObjectDTO(
            success=False,
            message=message,
            product_id=product_id,
            offer_id=offer_id,
            courier=policy.courier if policy is not None else None,
            restriction=policy.restriction if policy is not None else None,
            partida=policy.partida if policy is not None else None,
            cabys=policy.cabys if policy is not None else None,
            quotation_notes=notes,
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
        price_without_tax_dolar: Decimal,
        price_without_tax_local: Decimal,
        special_amazon_price: Decimal | None = None,
        special_price_dolar: Decimal | None = None,
        special_price_local: Decimal | None = None,
        special_price_without_tax_dolar: Decimal | None = None,
        special_price_without_tax_local: Decimal | None = None,
        offer_id: str = "",
        delivery_promise_amz: int | None = None,
        quotation_notes: Sequence[str] | None = None,
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
            price_without_tax_dolar: Calculator sale price in USD before
                sales VAT.
            price_without_tax_local: Calculator sale price in local currency
                before sales VAT.
            special_amazon_price: Special Amazon USD price, or ``None``.
            special_price_dolar: Quoted special sale price in USD, or ``None``.
            special_price_local: Quoted special local price, or ``None``.
            special_price_without_tax_dolar: Special sale price in USD before
                sales VAT, or ``None``.
            special_price_without_tax_local: Special sale price in local
                currency before sales VAT, or ``None``.
            offer_id: Selected Amazon offer identifier when applicable.
            delivery_promise_amz: Country promise tier when an offer exists.
            quotation_notes: Extra decisions after policy resolution.

        Returns:
            ResultObjectDTO: Successful product quotation.
        """
        notes = (
            tuple(quotation_notes)
            if quotation_notes is not None
            else policy.quotation_notes
        )
        return ResultObjectDTO(
            success=True,
            message="Product quoted successfully.",
            product_id=product_id,
            offer_id=offer_id,
            amazon_price=amazon_price,
            special_amazon_price=special_amazon_price,
            price_dolar=price_dolar,
            price_local=price_local,
            price_without_tax_dolar=price_without_tax_dolar,
            price_without_tax_local=price_without_tax_local,
            special_price_dolar=special_price_dolar,
            special_price_local=special_price_local,
            special_price_without_tax_dolar=special_price_without_tax_dolar,
            special_price_without_tax_local=special_price_without_tax_local,
            cost_dolar=cost_dolar,
            cost_local=cost_dolar * exchange_rate,
            exchange_rate=exchange_rate,
            currency_code=settings.require("currency_code"),
            delivery_promise_amz=delivery_promise_amz,
            courier=policy.courier,
            restriction=policy.restriction,
            partida=policy.partida,
            cabys=policy.cabys,
            quotation_notes=notes,
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


def _unspsc_summary(data: UnspscDataDTO) -> str:
    """Format the UNSPSC fields already selected by the caller."""
    return (
        f"arancel {data.arancel_percentage}, courier {data.courier}, "
        f"restricción {data.restriction}, margen {data.margin_percentage}, "
        f"peligroso {data.danger_good_active}"
    )


def _unpack_sales_iva(value: object) -> tuple[Decimal, tuple[str, ...]]:
    """Accept SalesIvaDTO or a bare Decimal from tests and Strategies."""
    notes = getattr(value, "quotation_notes", None)
    rate = getattr(value, "rate", value)
    return rate, tuple(notes or ())


def _unexpected_product_error_message() -> str:
    """Return the public message for an unexpected product failure.

    Returns:
        str: Fixed generic message with no exception type or detail.
    """
    return "Unexpected product quotation failure."
