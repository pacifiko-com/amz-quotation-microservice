"""Granular contract for country-specific quotation decisions."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
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
    def load_products(self, product_ids: Sequence[int]) -> dict[int, ProductDataDTO]:
        """Load product rows for a request batch.

        Args:
            product_ids: Pacifiko identifiers in the current request.

        Returns:
            dict[int, ProductDataDTO]: Product data keyed by identifier.
                Missing rows are omitted so the caller can apply an empty DTO.
        """
        raise NotImplementedError

    @abstractmethod
    def load_unspsc_map(
        self,
        codes: Sequence[str],
        settings: CountrySettingsDTO,
    ) -> dict[str, UnspscDataDTO | None]:
        """Load UNSPSC policy for every distinct code in the request.

        Args:
            codes: Product classification codes.
            settings: Defaults for nullable policy columns.

        Returns:
            dict[str, UnspscDataDTO | None]: ``None`` marks an unknown code.
        """
        raise NotImplementedError

    @abstractmethod
    def save_unknown_unspsc_many(self, codes: Sequence[str]) -> None:
        """Record every unknown UNSPSC in one pass.

        Args:
            codes: Classifications absent from ``oc_arancel_amz``.

        Returns:
            None: Each code is inserted idempotently.
        """
        raise NotImplementedError

    @abstractmethod
    def load_tariffs(self, partidas: Sequence[str]) -> dict[str, TariffDataDTO]:
        """Load tariff rows for the distinct codes used in the request.

        Args:
            partidas: National tariff codes after request overrides.

        Returns:
            dict[str, TariffDataDTO]: Existing tariff policy keyed by code.
        """
        raise NotImplementedError

    @abstractmethod
    def load_category_tree_courier_map(
        self,
        product_ids: Sequence[int],
    ) -> dict[int, bool]:
        """Load category-tree courier flags for the products that still need it.

        Args:
            product_ids: Products whose courier is still unset after UNSPSC
                and tariff resolution.

        Returns:
            dict[int, bool]: Courier flag keyed by product identifier.
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
    def load_sales_iva_map(self, codes: Sequence[str]) -> dict[str, Decimal]:
        """Load sales-VAT rates keyed by country classification code.

        Args:
            codes: Product classification codes used to resolve sales VAT.

        Returns:
            dict[str, Decimal]: Known rates. Missing codes use the country
                default later.
        """
        raise NotImplementedError

    @abstractmethod
    def resolve_sales_iva_rate(
        self,
        cabys: str | None,
        settings: CountrySettingsDTO,
    ) -> Decimal:
        """Return the sales-VAT rate for one product classification.

        Args:
            cabys: Optional country classification used for sales VAT.
            settings: Fallback rate from ``oc_setting``.

        Returns:
            Decimal: Rate applied by the country calculator.
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
