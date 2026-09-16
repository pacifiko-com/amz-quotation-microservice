"""Costa Rica-specific OpenCart repository."""

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
from decimal import Decimal


class CostaRicaQuotationRepository(BaseQuotationRepository):
    """Load product and tariff data using the Costa Rica schema."""

    COUNTRY = "CR"

    def get_product(self, product_id: int) -> ProductDataDTO:
        """Load Pacifiko values that request overrides may replace.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            ProductDataDTO: Weight, courier and national tariff code.
        """
        return self.get_products((product_id,)).get(product_id, EMPTY_PRODUCT_DATA)

    def get_products(self, product_ids: Sequence[int]) -> dict[int, ProductDataDTO]:
        """Load Costa Rica product rows for a request batch.

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
            SELECT product_id, weight, courier, partida_codigo, cabys
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
        """Load Costa Rica DAI and ISC for a national tariff code.

        Args:
            partida: Canonical tariff code or ``None``.

        Returns:
            TariffDataDTO | None: DAI/ISC row when the assigned code exists.
        """
        if not partida:
            return None
        return self.get_tariffs((partida,)).get(partida)

    def get_tariffs(self, partidas: Sequence[str]) -> dict[str, TariffDataDTO]:
        """Load Costa Rica tariff rows for a request batch.

        Args:
            partidas: National tariff codes after request overrides.

        Returns:
            dict[str, TariffDataDTO]: Existing tariff policy keyed by code.
        """
        unique_codes = tuple(dict.fromkeys(code for code in partidas if code))
        if not unique_codes:
            return {}

        placeholders = ", ".join(["%s"] * len(unique_codes))
        sql = f"""
            SELECT partida, dai, isc, courier, restriccion
            FROM oc_partida_arancelaria
            WHERE partida IN ({placeholders})
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, unique_codes)
            rows = cursor.fetchall()
        return {str(row["partida"]): self._tariff_from_row(row) for row in rows}

    def get_cabys_tax_rates(self, codes: Sequence[str]) -> dict[str, Decimal]:
        """Load Costa Rica CABYS sales-VAT rates for a request batch.

        Args:
            codes: CABYS identifiers assigned to products.

        Returns:
            dict[str, Decimal]: ``tax_rate`` keyed by CABYS when the row
                exists and the rate is not null.
        """
        unique_codes = tuple(dict.fromkeys(code for code in codes if code))
        if not unique_codes:
            return {}

        placeholders = ", ".join(["%s"] * len(unique_codes))
        sql = f"""
            SELECT cabys, tax_rate
            FROM pac_cabys
            WHERE cabys IN ({placeholders})
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, unique_codes)
            rows = cursor.fetchall()
        return {
            str(row["cabys"]): decimal_from_row(row["tax_rate"], "tax_rate")
            for row in rows
            if row["tax_rate"] is not None
        }

    @staticmethod
    def _product_from_row(row: dict) -> ProductDataDTO:
        """Map one Costa Rica ``oc_product`` row.

        Args:
            row: Database record that includes weight, courier and tariff code.

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
            partida=str(row["partida_codigo"]).strip() if row["partida_codigo"] else None,
            cabys=str(row["cabys"]).strip() if row.get("cabys") else None,
        )

    @staticmethod
    def _tariff_from_row(row: dict) -> TariffDataDTO:
        """Map one Costa Rica ``oc_partida_arancelaria`` row.

        Args:
            row: Database record for a national tariff code.

        Returns:
            TariffDataDTO: DAI/ISC row used by the Costa Rica Strategy.
        """
        return TariffDataDTO(
            partida=str(row["partida"]),
            dai=decimal_from_row(row["dai"], "dai") if row["dai"] is not None else None,
            isc=decimal_from_row(row["isc"], "isc") if row["isc"] is not None else None,
            courier=bool(row["courier"]),
            restriction=int(row["restriccion"]),
        )
