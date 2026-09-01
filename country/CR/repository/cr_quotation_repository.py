"""Costa Rica-specific OpenCart repository."""

from __future__ import annotations

from database.connection import get_connection
from DTO.quotation_context_dto import (
    ProductDataDTO,
    TariffDataDTO,
    decimal_from_row,
)
from repository.base_quotation_repository import BaseQuotationRepository


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
        sql = """
            SELECT weight, courier, partida_codigo
            FROM oc_product
            WHERE product_id = %s
            LIMIT 1
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, (product_id,))
            row = cursor.fetchone()

        if not row:
            return ProductDataDTO(weight_kg=None, courier=None, partida=None)
        return ProductDataDTO(
            weight_kg=(
                decimal_from_row(row["weight"], "weight")
                if row["weight"] is not None
                else None
            ),
            courier=bool(row["courier"]) if row["courier"] is not None else None,
            partida=str(row["partida_codigo"]).strip() if row["partida_codigo"] else None,
        )

    def get_tariff(self, partida: str | None) -> TariffDataDTO | None:
        """Load Costa Rica DAI and ISC for a national tariff code.

        Args:
            partida: Canonical tariff code or ``None``.

        Returns:
            TariffDataDTO | None: DAI/ISC row when the assigned code exists.
        """
        if not partida:
            return None
        sql = """
            SELECT partida, dai, isc, courier, restriccion
            FROM oc_partida_arancelaria
            WHERE partida = %s
            LIMIT 1
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, (partida,))
            row = cursor.fetchone()
        if not row:
            return None
        return TariffDataDTO(
            partida=str(row["partida"]),
            dai=decimal_from_row(row["dai"], "dai") if row["dai"] is not None else None,
            isc=decimal_from_row(row["isc"], "isc") if row["isc"] is not None else None,
            courier=bool(row["courier"]),
            restriction=int(row["restriccion"]),
        )
