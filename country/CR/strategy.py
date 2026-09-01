"""Granular Costa Rica quotation Strategy."""

from __future__ import annotations

from decimal import Decimal

from country.country_strategy_abstract import CountryQuotationStrategy
from country.CR.repository.cr_quotation_repository import CostaRicaQuotationRepository
from country.CR.service.cr_quotation_service import (
    CR_SETTING_KEYS,
    CostaRicaQuotationService,
)
from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    ProductDataDTO,
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
        return self._repository.get_settings(CR_SETTING_KEYS)

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

    def resolve_tariff_data(self, partida: str | None) -> TariffDataDTO | None:
        """Resolve and normalize a Costa Rica tariff row.

        Args:
            partida: National tariff code or ``None``.

        Returns:
            TariffDataDTO | None: Row with DAI + ISC exposed as the common
                ``arancel_percentage`` while retaining courier/restriction.
        """
        tariff = self._repository.get_tariff(partida)
        if tariff is None:
            return None

        # CR no guarda un arancel único como GT, sino DAI e ISC por separado.
        # Aquí se suman para exponer el mismo campo que espera el flujo común,
        # conservando los valores originales para trazabilidad. Si ambos vienen
        # nulos se deja en None, para que el flujo respete el arancel del
        # UNSPSC en lugar de asumir cero.
        arancel = None
        if tariff.dai is not None or tariff.isc is not None:
            arancel = (tariff.dai or Decimal("0")) + (tariff.isc or Decimal("0"))
        return TariffDataDTO(
            partida=tariff.partida,
            arancel_percentage=arancel,
            dai=tariff.dai,
            isc=tariff.isc,
            courier=tariff.courier,
            restriction=tariff.restriction,
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
