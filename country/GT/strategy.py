"""Granular Guatemala quotation Strategy."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from country.GT.repository.gt_quotation_repository import (
    GuatemalaQuotationRepository,
)
from country.GT.service.gt_quotation_service import (
    GT_SETTING_KEYS,
    GuatemalaQuotationService,
)
from database.settings_cache import resolve_cached_settings
from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    ProductDataDTO,
    SalesIvaDTO,
    SelectedOfferDTO,
    TariffDataDTO,
    UnspscDataDTO,
)


class GuatemalaQuotationStrategy(CountryQuotationStrategy):
    """Resolve only Guatemala-specific data and calculations."""

    # Esta clase es el punto de entrada de GT al flujo común: no contiene
    # lógica propia, solo enruta cada operación del contrato a su
    # implementación real. Las consultas viven en GuatemalaQuotationRepository
    # y las fórmulas en GuatemalaQuotationService.

    def __init__(
        self,
        repository: GuatemalaQuotationRepository | None = None,
        service: GuatemalaQuotationService | None = None,
    ) -> None:
        """Create the Strategy with injectable country collaborators.

        Args:
            repository: Guatemala OpenCart data adapter.
            service: Guatemala calculator and promise service.
        """
        self._repository = repository or GuatemalaQuotationRepository()
        self._service = service or GuatemalaQuotationService()

    def resolve_settings(self) -> CountrySettingsDTO:
        """Load all GT settings required by the common flow.

        Returns:
            CountrySettingsDTO: Typed GT ``global_store`` settings.
        """
        # Se piden solo las keys que GT usa, no toda la tabla, para que falte
        # de forma explícita cualquier constante no provisionada.
        return resolve_cached_settings(
            "GT",
            lambda: self._repository.get_settings(GT_SETTING_KEYS),
        )

    def get_unspsc_data(
        self,
        unspsc: str,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO | None:
        """Find Guatemala import policy for an UNSPSC.

        Args:
            unspsc: Product classification code.
            settings: Defaults used for nullable columns.

        Returns:
            UnspscDataDTO | None: Existing policy or ``None``.
        """
        return self._repository.find_unspsc(unspsc, settings)

    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Record an unknown Guatemala UNSPSC.

        Args:
            unspsc: Classification missing from ``oc_arancel_amz``.

        Returns:
            None: The code is inserted idempotently.
        """
        self._repository.save_unknown_unspsc(unspsc)

    def get_default_unspsc(
        self,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO:
        """Build GT defaults for an unknown UNSPSC.

        Args:
            settings: Country constants from ``oc_setting``.

        Returns:
            UnspscDataDTO: Fully configured default policy.
        """
        return self._repository.get_default_unspsc(settings)

    def get_product_data(self, product_id: int) -> ProductDataDTO:
        """Load GT product weight, courier and SAC code.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            ProductDataDTO: Product data used when no override is supplied.
        """
        return self._repository.get_product(product_id)

    def load_products(self, product_ids: Sequence[int]) -> dict[int, ProductDataDTO]:
        """Load Guatemala product rows for the whole request.

        Args:
            product_ids: Pacifiko identifiers in the current request.

        Returns:
            dict[int, ProductDataDTO]: Product data keyed by identifier.
        """
        return self._repository.get_products(product_ids)

    def load_unspsc_map(
        self,
        codes: Sequence[str],
        settings: CountrySettingsDTO,
    ) -> dict[str, UnspscDataDTO | None]:
        """Load Guatemala UNSPSC policy for the whole request.

        Args:
            codes: Product classification codes.
            settings: Defaults used for nullable columns.

        Returns:
            dict[str, UnspscDataDTO | None]: ``None`` marks an unknown code.
        """
        return self._repository.find_unspsc_map(codes, settings)

    def save_unknown_unspsc_many(self, codes: Sequence[str]) -> None:
        """Record unknown Guatemala UNSPSC codes in one statement.

        Args:
            codes: Classifications missing from ``oc_arancel_amz``.

        Returns:
            None: Codes are inserted idempotently.
        """
        self._repository.save_unknown_unspsc_many(codes)

    def load_tariffs(self, partidas: Sequence[str]) -> dict[str, TariffDataDTO]:
        """Load Guatemala tariff rows for the whole request.

        Args:
            partidas: SAC codes after request overrides.

        Returns:
            dict[str, TariffDataDTO]: Existing tariff policy keyed by code.
        """
        return {
            partida: _annotate_gt_tariff(tariff)
            for partida, tariff in self._repository.get_tariffs(partidas).items()
        }

    def load_sales_iva_map(self, codes: Sequence[str]) -> dict[str, Decimal]:
        """Guatemala does not resolve sales VAT from a CABYS catalog.

        Args:
            codes: Unused classification codes.

        Returns:
            dict[str, Decimal]: Empty map; the calculator uses the setting.
        """
        del codes
        return {}

    def resolve_sales_iva_rate(
        self,
        cabys: str | None,
        settings: CountrySettingsDTO,
    ) -> SalesIvaDTO:
        """Return the configured Guatemala sales-VAT rate.

        Args:
            cabys: Unused in Guatemala.
            settings: ``default_iva_venta`` from ``oc_setting``.

        Returns:
            SalesIvaDTO: Sales-VAT rate used by ``calculate()``.
        """
        del cabys
        rate = settings.decimal("default_iva_venta")
        return SalesIvaDTO(
            rate=rate,
            quotation_notes=(
                f"IVA de venta GT desde oc_setting default_iva_venta ({rate}).",
            ),
        )

    def load_category_tree_courier_map(
        self,
        product_ids: Sequence[int],
    ) -> dict[int, bool]:
        """Load Guatemala category-tree courier flags in one query.

        Args:
            product_ids: Products whose courier is still unset.

        Returns:
            dict[int, bool]: Courier flag keyed by product identifier.
        """
        return self._repository.get_category_tree_courier_map(product_ids)

    def resolve_tariff_data(self, partida: str | None) -> TariffDataDTO | None:
        """Resolve the assigned Guatemala SAC row.

        Args:
            partida: SAC code or ``None``.

        Returns:
            TariffDataDTO | None: Normalized tariff policy.
        """
        return _annotate_gt_tariff(self._repository.get_tariff(partida))

    def get_category_tree_courier(self, product_id: int) -> bool:
        """Resolve GT courier from the product category tree.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            bool: Deepest applicable category courier flag.
        """
        return self._repository.get_category_tree_courier(product_id)

    def resolve_exchange_rate(self, settings: CountrySettingsDTO) -> Decimal:
        """Resolve USD-to-GTQ exchange rate.

        Args:
            settings: GT settings loaded for the request.

        Returns:
            Decimal: Current configured exchange rate.
        """
        # GT toma la tasa de oc_setting; se mantiene como operación del
        # contrato porque otros países pueden requerir una fuente distinta.
        return settings.decimal("tipo_de_cambio")

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Run the Guatemala calculator.

        Args:
            calculation: Normalized common-flow calculation input.

        Returns:
            CalculationResultDTO: GT landed cost and local price.
        """
        return self._service.calculate(calculation)

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> int:
        """Resolve Guatemala Amazon promise.

        Args:
            offer: Selected normalized Amazon offer.
            courier: Resolved courier flag.
            settings: GT promise constants.

        Returns:
            int: Delivery promise tier.
        """
        return self._service.resolve_delivery_promise(offer, courier, settings)


def _annotate_gt_tariff(tariff: TariffDataDTO | None) -> TariffDataDTO | None:
    """Attach the lookup note at the GT tariff resolution site."""
    if tariff is None:
        return None
    return replace(
        tariff,
        quotation_notes=(
            f"Partida {tariff.partida} cargada de oc_partida_arancelaria "
            f"(arancel {tariff.arancel_percentage}, courier {tariff.courier}, "
            f"restricción {tariff.restriction}).",
        ),
    )
