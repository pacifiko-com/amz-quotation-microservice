"""Granular contract for country-specific quotation decisions."""

from abc import ABC, abstractmethod
from decimal import Decimal

from DTO.quotation_context_dto import (
    CalculationInputDTO,
    CalculationResultDTO,
    CountrySettingsDTO,
    ProductDataDTO,
    SelectedOfferDTO,
    TariffDataDTO,
    UnspscDataDTO,
)


class CountryQuotationStrategy(ABC):
    """Resolve only the decisions that can vary by country."""

    # Este contrato NO cotiza un producto completo. El flujo de cotización vive
    # en QuotationService y es igual para todos los países; aquí solo se
    # declaran los puntos donde GT, CR y los países futuros difieren: esquema de
    # base de datos, constantes, tasa de cambio, fórmula de cálculo y promesa.
    # Agregar un país = implementar estas operaciones, sin tocar el flujo común.

    @abstractmethod
    def resolve_settings(self) -> CountrySettingsDTO:
        """Load the country constants required by quotation.

        Returns:
            CountrySettingsDTO: Typed ``global_store`` values.
        """
        raise NotImplementedError

    @abstractmethod
    def get_unspsc_data(
        self,
        unspsc: str,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO | None:
        """Return policy for an UNSPSC or ``None`` when unknown.

        Args:
            unspsc: Product classification code.
            settings: Defaults for nullable policy columns.

        Returns:
            UnspscDataDTO | None: Existing country policy.
        """
        raise NotImplementedError

    @abstractmethod
    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Record an unknown UNSPSC in ``oc_category_amz_new``.

        Args:
            unspsc: Classification absent from ``oc_arancel_amz``.

        Returns:
            None: The country database records the code idempotently.
        """
        raise NotImplementedError

    @abstractmethod
    def get_default_unspsc(
        self,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO:
        """Return configured defaults for an unknown UNSPSC.

        Args:
            settings: Country constants from ``oc_setting``.

        Returns:
            UnspscDataDTO: Complete default policy.
        """
        raise NotImplementedError

    @abstractmethod
    def get_product_data(self, product_id: int) -> ProductDataDTO:
        """Return product weight, courier and tariff code.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            ProductDataDTO: Country-schema product projection.
        """
        raise NotImplementedError

    @abstractmethod
    def resolve_tariff_data(self, partida: str | None) -> TariffDataDTO | None:
        """Resolve a country tariff row when the code exists.

        Args:
            partida: Optional national tariff code.

        Returns:
            TariffDataDTO | None: Normalized country tariff policy.
        """
        raise NotImplementedError

    @abstractmethod
    def get_category_tree_courier(self, product_id: int) -> bool:
        """Resolve courier from the deepest applicable category.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            bool: Whether the category tree requires courier.
        """
        raise NotImplementedError

    @abstractmethod
    def resolve_exchange_rate(self, settings: CountrySettingsDTO) -> Decimal:
        """Return the configured USD-to-local exchange rate.

        Args:
            settings: Country settings loaded for the request.

        Returns:
            Decimal: USD-to-local exchange rate.
        """
        raise NotImplementedError

    @abstractmethod
    def calculate(self, calculation: CalculationInputDTO) -> CalculationResultDTO:
        """Run the country calculator using a normalized input DTO.

        Args:
            calculation: Amazon price, policy, exchange rate and settings.

        Returns:
            CalculationResultDTO: Landed cost USD and local sale price.
        """
        raise NotImplementedError

    @abstractmethod
    def resolve_delivery_promise(
        self,
        offer: SelectedOfferDTO,
        courier: bool,
        settings: CountrySettingsDTO,
    ) -> int:
        """Return the country-specific Amazon delivery promise tier.

        Args:
            offer: Selected normalized Amazon offer.
            courier: Resolved courier mode.
            settings: Country promise constants.

        Returns:
            int: OpenCart delivery promise tier.
        """
        raise NotImplementedError
