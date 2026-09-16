"""Granular Costa Rica quotation Strategy."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from country.CR.repository.cr_quotation_repository import CostaRicaQuotationRepository
from country.CR.service.cr_quotation_service import (
    CR_SETTING_KEYS,
    CostaRicaQuotationService,
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


class CostaRicaQuotationStrategy(CountryQuotationStrategy):
    """Resolve only Costa Rica-specific data and calculations."""

    # Esta clase es el punto de entrada de CR al flujo común: no contiene
    # lógica propia, solo enruta cada operación del contrato a su
    # implementación real. Las consultas viven en CostaRicaQuotationRepository
    # y las fórmulas en CostaRicaQuotationService.

    def __init__(
        self,
        repository: CostaRicaQuotationRepository | None = None,
        service: CostaRicaQuotationService | None = None,
    ) -> None:
        """Create the Strategy with injectable country collaborators.

        Args:
            repository: Costa Rica OpenCart data adapter.
            service: Costa Rica calculator and promise service.
        """
        self._repository = repository or CostaRicaQuotationRepository()
        self._service = service or CostaRicaQuotationService()

    def resolve_settings(self) -> CountrySettingsDTO:
        """Load all CR settings required by the common flow.

        Returns:
            CountrySettingsDTO: Typed CR ``global_store`` settings.
        """
        # Se piden solo las keys que CR usa, no toda la tabla, para que falte
        # de forma explícita cualquier constante no provisionada.
        return resolve_cached_settings(
            "CR",
            lambda: self._repository.get_settings(CR_SETTING_KEYS),
        )

    def get_unspsc_data(
        self,
        unspsc: str,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO | None:
        """Find Costa Rica import policy for an UNSPSC.

        Args:
            unspsc: Product classification code.
            settings: Defaults used for nullable columns.

        Returns:
            UnspscDataDTO | None: Existing policy or ``None``.
        """
        return self._repository.find_unspsc(unspsc, settings)

    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Record an unknown Costa Rica UNSPSC.

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
        """Build CR defaults for an unknown UNSPSC.

        Args:
            settings: Country constants from ``oc_setting``.

        Returns:
            UnspscDataDTO: Fully configured default policy.
        """
        return self._repository.get_default_unspsc(settings)

    def get_product_data(self, product_id: int) -> ProductDataDTO:
        """Load CR product weight, courier and tariff code.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            ProductDataDTO: Product data used when no override is supplied.
        """
        return self._repository.get_product(product_id)

    def load_products(self, product_ids: Sequence[int]) -> dict[int, ProductDataDTO]:
        """Load Costa Rica product rows for the whole request.

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
        """Load Costa Rica UNSPSC policy for the whole request.

        Args:
            codes: Product classification codes.
            settings: Defaults used for nullable columns.

        Returns:
            dict[str, UnspscDataDTO | None]: ``None`` marks an unknown code.
        """
        return self._repository.find_unspsc_map(codes, settings)

    def save_unknown_unspsc_many(self, codes: Sequence[str]) -> None:
        """Record unknown Costa Rica UNSPSC codes in one statement.

        Args:
            codes: Classifications missing from ``oc_arancel_amz``.

        Returns:
            None: Codes are inserted idempotently.
        """
        self._repository.save_unknown_unspsc_many(codes)

    def load_tariffs(self, partidas: Sequence[str]) -> dict[str, TariffDataDTO]:
        """Load and normalize Costa Rica tariff rows for the whole request.

        Args:
            partidas: National tariff codes after request overrides.

        Returns:
            dict[str, TariffDataDTO]: Normalized tariff policy keyed by code.
        """
        return {
            partida: self._normalize_tariff(tariff)
            for partida, tariff in self._repository.get_tariffs(partidas).items()
        }

    def load_sales_iva_map(self, codes: Sequence[str]) -> dict[str, Decimal]:
        """Load Costa Rica CABYS sales-VAT rates for the whole request.

        Args:
            codes: CABYS identifiers assigned to products.

        Returns:
            dict[str, Decimal]: Known ``pac_cabys.tax_rate`` values.
        """
        return self._repository.get_cabys_tax_rates(codes)

    def resolve_sales_iva_rate(
        self,
        cabys: str | None,
        settings: CountrySettingsDTO,
    ) -> SalesIvaDTO:
        """Use ``pac_cabys.tax_rate`` when assigned; otherwise the default.

        Args:
            cabys: Product CABYS or ``None``.
            settings: Fallback ``default_iva_venta``.

        Returns:
            SalesIvaDTO: Sales-VAT rate used by ``calculate()``.
        """
        default_rate = settings.decimal("default_iva_venta")
        if not cabys:
            return SalesIvaDTO(
                rate=default_rate,
                quotation_notes=(
                    "Sin CABYS; IVA de venta desde oc_setting "
                    f"default_iva_venta ({default_rate}).",
                ),
            )
        rates = self._repository.get_cabys_tax_rates((cabys,))
        if cabys in rates:
            rate = rates[cabys]
            return SalesIvaDTO(
                rate=rate,
                quotation_notes=(
                    f"IVA de venta {rate} tomado de pac_cabys para CABYS {cabys}.",
                ),
            )
        return SalesIvaDTO(
            rate=default_rate,
            quotation_notes=(
                f"CABYS {cabys} no encontrado en pac_cabys; IVA de venta "
                f"desde oc_setting default_iva_venta ({default_rate}).",
            ),
        )

    def load_category_tree_courier_map(
        self,
        product_ids: Sequence[int],
    ) -> dict[int, bool]:
        """Load Costa Rica category-tree courier flags in one query.

        Args:
            product_ids: Products whose courier is still unset.

        Returns:
            dict[int, bool]: Courier flag keyed by product identifier.
        """
        return self._repository.get_category_tree_courier_map(product_ids)

    def resolve_tariff_data(self, partida: str | None) -> TariffDataDTO | None:
        """Resolve and normalize a Costa Rica tariff row.

        Args:
            partida: National tariff code or ``None``.

        Returns:
            TariffDataDTO | None: Row with DAI + ISC exposed as the common
                ``arancel_percentage`` while retaining courier/restriction.
        """
        return self._normalize_tariff(self._repository.get_tariff(partida))

    @staticmethod
    def _normalize_tariff(tariff: TariffDataDTO | None) -> TariffDataDTO | None:
        """Expose DAI + ISC as the common arancel field.

        Args:
            tariff: Raw Costa Rica tariff row or ``None``.

        Returns:
            TariffDataDTO | None: Normalized tariff used by the common flow.
        """
        if tariff is None:
            return None

        notes: list[str] = []
        # CR no guarda un arancel único como GT, sino DAI e ISC por separado.
        # Aquí se suman para exponer el mismo campo que espera el flujo común,
        # conservando los valores originales para trazabilidad. Si ambos vienen
        # nulos se deja en None, para que el flujo respete el arancel del
        # UNSPSC en lugar de asumir cero.
        arancel = None
        if tariff.dai is not None or tariff.isc is not None:
            arancel = (tariff.dai or Decimal("0")) + (tariff.isc or Decimal("0"))
            notes.append(
                f"Arancel de partida = DAI ({tariff.dai}) + ISC ({tariff.isc}) "
                f"= {arancel}."
            )
        else:
            notes.append(
                "Partida CR sin DAI/ISC; el arancel se conservará del UNSPSC."
            )
        return TariffDataDTO(
            partida=tariff.partida,
            arancel_percentage=arancel,
            dai=tariff.dai,
            isc=tariff.isc,
            courier=tariff.courier,
            restriction=tariff.restriction,
            quotation_notes=tuple(notes),
        )

    def get_category_tree_courier(self, product_id: int) -> bool:
        """Resolve CR courier from the product category tree.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            bool: Deepest applicable category courier flag.
        """
        return self._repository.get_category_tree_courier(product_id)

    def resolve_exchange_rate(self, settings: CountrySettingsDTO) -> Decimal:
        """Resolve USD-to-CRC exchange rate.

        Args:
            settings: CR settings loaded for the request.

        Returns:
            Decimal: Current configured exchange rate.
        """
        # CR toma la tasa de oc_setting; se mantiene como operación del
        # contrato porque otros países pueden requerir una fuente distinta.
        return settings.decimal("tipo_de_cambio")

    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Run the Costa Rica calculator.

        Args:
            calculation: Normalized common-flow calculation input.

        Returns:
            CalculationResultDTO: CR landed cost and local price.
        """
        return self._service.calculate(calculation)

    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> int:
        """Resolve Costa Rica Amazon promise.

        Args:
            offer: Selected normalized Amazon offer.
            courier: Resolved courier flag.
            settings: CR promise constants.

        Returns:
            int: Delivery promise tier.
        """
        return self._service.resolve_delivery_promise(offer, courier, settings)
