"""Shared MySQL operations whose schema is equal in GT and CR."""

from __future__ import annotations

from collections.abc import Sequence

from database.connection import get_connection
from DTO.quotation_context_dto import (
    CountrySettingsDTO,
    UnspscDataDTO,
    decimal_from_row,
)


class BaseQuotationRepository:
    """Read shared OpenCart policy tables and record unknown UNSPSC codes."""

    # Estas consultas son idénticas en GT y CR, por eso viven aquí. Cada
    # repositorio de país declara su COUNTRY y con eso se enruta la conexión a
    # la base de datos correspondiente.
    COUNTRY: str

    def get_settings(self, keys: Sequence[str]) -> CountrySettingsDTO:
        """Load selected ``global_store`` settings.

        Args:
            keys: Exact setting names required by the country Strategy.

        Returns:
            CountrySettingsDTO: Key/value map. Missing keys remain absent so
                typed access can report a configuration error.
        """
        if not keys:
            return CountrySettingsDTO(values={})

        placeholders = ", ".join(["%s"] * len(keys))
        sql = f"""
            SELECT `key`, value
            FROM oc_setting
            WHERE code = 'global_store' AND `key` IN ({placeholders})
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, tuple(keys))
            rows = cursor.fetchall()
        return CountrySettingsDTO(
            values={str(row["key"]): str(row["value"]) for row in rows}
        )

    def find_unspsc(
        self,
        unspsc: str,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO | None:
        """Find UNSPSC policy without applying missing-code behavior.

        Args:
            unspsc: Required product classification code.
            settings: Country defaults loaded from ``oc_setting``.

        Returns:
            UnspscDataDTO | None: Existing policy, or ``None`` when the code
                must be recorded and defaulted by the common flow.
        """
        sql = """
            SELECT
                arancel_porcentage,
                restriction,
                arancel_category_cod,
                margin_porcentage,
                danger_good_active,
                courier
            FROM oc_arancel_amz
            WHERE arancel_category = %s
            LIMIT 1
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, (unspsc,))
            row = cursor.fetchone()

        # Se devuelve None en vez de un default para que el flujo común pueda
        # distinguir el código inexistente y registrarlo antes de aplicarlo.
        if not row:
            return None

        # La fila puede existir con columnas nulas; cada campo cae a su default
        # de forma independiente en lugar de descartar la fila completa.
        return UnspscDataDTO(
            arancel_percentage=(
                decimal_from_row(row["arancel_porcentage"], "arancel_porcentage")
                if row["arancel_porcentage"] is not None
                else settings.decimal("default_arancel")
            ),
            restriction=(
                int(row["restriction"])
                if row["restriction"] is not None
                else settings.integer("default_restriction")
            ),
            category_code=(
                int(row["arancel_category_cod"])
                if row["arancel_category_cod"] is not None
                else settings.integer("default_arancel_category_cod")
            ),
            margin_percentage=(
                decimal_from_row(row["margin_porcentage"], "margin_porcentage")
                if row["margin_porcentage"] is not None
                else settings.decimal("margen")
            ),
            danger_good_active=(
                bool(row["danger_good_active"])
                if row["danger_good_active"] is not None
                else settings.boolean("default_danger_good_active")
            ),
            courier=(
                bool(row["courier"])
                if row["courier"] is not None
                else settings.boolean("default_courier")
            ),
        )

    @staticmethod
    def get_default_unspsc(settings: CountrySettingsDTO) -> UnspscDataDTO:
        """Build the configured policy for an unknown UNSPSC.

        Args:
            settings: Country defaults loaded from ``oc_setting``.

        Returns:
            UnspscDataDTO: Default arancel, restriction, category, margin,
                dangerous-goods and courier values.
        """
        return UnspscDataDTO(
            arancel_percentage=settings.decimal("default_arancel"),
            restriction=settings.integer("default_restriction"),
            category_code=settings.integer("default_arancel_category_cod"),
            margin_percentage=settings.decimal("margen"),
            danger_good_active=settings.boolean("default_danger_good_active"),
            courier=settings.boolean("default_courier"),
        )

    def get_category_tree_courier(self, product_id: int) -> bool:
        """Resolve courier from the deepest flagged product category.

        Args:
            product_id: Pacifiko product identifier.

        Returns:
            bool: True when the product category tree contains a courier flag.
        """
        # Se recorre la ruta completa de categorías y se ordena por nivel
        # descendente, así gana la categoría más específica marcada como
        # courier en lugar de la más general.
        sql = """
            SELECT c.courier
            FROM oc_product_to_category AS pc
            INNER JOIN oc_category_path AS cp
                ON cp.category_id = pc.category_id
            INNER JOIN oc_category AS c
                ON c.category_id = cp.path_id
            WHERE pc.product_id = %s AND c.courier = 1
            ORDER BY cp.level DESC
            LIMIT 1
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, (product_id,))
            row = cursor.fetchone()
        return bool(row and row["courier"])

    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Insert an unknown UNSPSC idempotently.

        Args:
            unspsc: Classification absent from ``oc_arancel_amz``.

        Returns:
            None: ``oc_category_amz_new`` contains the code after the call.
        """
        # INSERT IGNORE evita duplicados cuando varios productos del mismo
        # request comparten el mismo UNSPSC desconocido.
        sql = """
            INSERT IGNORE INTO oc_category_amz_new (category_amz)
            VALUES (%s)
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, (unspsc,))
