"""Guatemala-specific OpenCart repository."""

from __future__ import annotations

from collections.abc import Sequence

from database.connection import get_connection
from DTO.quotation_context_dto import (
    EMPTY_PRODUCT_DATA,
    ProductDataDTO,
    TariffDataDTO,
    decimal_from_row,
)
from repository.base_quotation_repository import BaseQuotationRepository


class GuatemalaQuotationRepository(BaseQuotationRepository):
    """Load product and tariff data using the Guatemala schema."""

    COUNTRY = "GT"

    def get_product(self, product_id: int) -> ProductDataDTO:
        """Load Pacifiko values that request overrides may replace.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            ProductDataDTO: Weight, courier and SAC code. Missing products
                produce an empty DTO so request overrides can still be used.
        """
        return self.get_products((product_id,)).get(product_id, EMPTY_PRODUCT_DATA)

    def get_products(self, product_ids: Sequence[int]) -> dict[int, ProductDataDTO]:
        """Load Guatemala product rows for a request batch.

        Args:
            product_ids: Pacifiko identifiers in the current request.

        Returns:
            dict[int, ProductDataDTO]: Product data keyed by identifier.
        """
        unique_ids = tuple(dict.fromkeys(product_ids))
        if not unique_ids:
            return {}

        placeholders = ", ".join(["%s"] * len(unique_ids))
        sql = f"""
            SELECT product_id, weight, courier, partida_sac_codigo
            FROM oc_product
            WHERE product_id IN ({placeholders})
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, unique_ids)
            rows = cursor.fetchall()
        return {
            int(row["product_id"]): self._product_from_row(row)
            for row in rows
        }

    def get_tariff(self, partida: str | None) -> TariffDataDTO | None:
        """Load Guatemala trade policy for a SAC code.

        Args:
            partida: Canonical tariff code or ``None``.

        Returns:
            TariffDataDTO | None: Arancel, courier and restriction when the
                assigned code exists.
        """
        if not partida:
            return None
        return self.get_tariffs((partida,)).get(partida)

    def get_tariffs(self, partidas: Sequence[str]) -> dict[str, TariffDataDTO]:
        """Load Guatemala tariff rows for a request batch.

        Args:
            partidas: SAC codes after request overrides.

        Returns:
            dict[str, TariffDataDTO]: Existing tariff policy keyed by code.
        """
        unique_codes = tuple(dict.fromkeys(code for code in partidas if code))
        if not unique_codes:
            return {}

        placeholders = ", ".join(["%s"] * len(unique_codes))
        sql = f"""
            SELECT partida, arancel_porcentaje, courier, restriccion
            FROM oc_partida_arancelaria
            WHERE partida IN ({placeholders})
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, unique_codes)
            rows = cursor.fetchall()
        return {str(row["partida"]): self._tariff_from_row(row) for row in rows}

    @staticmethod
    def _product_from_row(row: dict) -> ProductDataDTO:
        """Map one Guatemala ``oc_product`` row.

        Args:
            row: Database record that includes weight, courier and SAC code.

        Returns:
            ProductDataDTO: Product projection used by the common flow.
        """
        return ProductDataDTO(
            weight_lb=(
                decimal_from_row(row["weight"], "weight")
                if row["weight"] is not None
                else None
            ),
            courier=bool(row["courier"]) if row["courier"] is not None else None,
            partida=(
                str(row["partida_sac_codigo"]).strip()
                if row["partida_sac_codigo"]
                else None
            ),
        )

    @staticmethod
    def _tariff_from_row(row: dict) -> TariffDataDTO:
        """Map one Guatemala ``oc_partida_arancelaria`` row.

        Args:
            row: Database record for a SAC code.

        Returns:
            TariffDataDTO: Normalized Guatemala tariff policy.
        """
        return TariffDataDTO(
            partida=str(row["partida"]),
            arancel_percentage=(
                decimal_from_row(row["arancel_porcentaje"], "arancel_porcentaje")
                if row["arancel_porcentaje"] is not None
                else None
            ),
            courier=bool(row["courier"]) if row["courier"] is not None else None,
            restriction=int(row["restriccion"]) if row["restriccion"] is not None else None,
        )
