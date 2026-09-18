"""Shared MySQL operations whose schema is equal in GT and CR."""

from __future__ import annotations

from collections.abc import Sequence

from database.connection import get_connection
from Utils.logger import get_logger
from DTO.quotation_context_dto import (
    CountrySettingsDTO,
    UnspscDataDTO,
    decimal_from_row,
)


logger = get_logger(__name__)


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
        return self.find_unspsc_map((unspsc,), settings).get(unspsc)

    def find_unspsc_map(
        self,
        codes: Sequence[str],
        settings: CountrySettingsDTO,
    ) -> dict[str, UnspscDataDTO | None]:
        """Load UNSPSC policy for every distinct code in one query.

        Args:
            codes: Product classification codes from the request.
            settings: Country defaults loaded from ``oc_setting``.

        Returns:
            dict[str, UnspscDataDTO | None]: ``None`` when the code is new.
        """
        unique_codes = tuple(dict.fromkeys(code for code in codes if code))
        if not unique_codes:
            return {}

        placeholders = ", ".join(["%s"] * len(unique_codes))
        sql = f"""
            SELECT
                arancel_category,
                arancel_porcentage,
                restriction,
                arancel_category_cod,
                margin_porcentage,
                danger_good_active,
                courier
            FROM oc_arancel_amz
            WHERE arancel_category IN ({placeholders})
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, unique_codes)
            rows = cursor.fetchall()

        found: dict[str, UnspscDataDTO] = {}
        for row in rows:
            code = str(row["arancel_category"])
            found[code] = self._unspsc_from_row(row, settings)
        return {code: found.get(code) for code in unique_codes}

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
        return self.get_category_tree_courier_map((product_id,)).get(product_id, False)

    def get_category_tree_courier_map(
        self,
        product_ids: Sequence[int],
    ) -> dict[int, bool]:
        """Resolve category-tree courier for many products in one query.

        Args:
            product_ids: Pacifiko identifiers that still need a courier flag.

        Returns:
            dict[int, bool]: Deepest courier flag keyed by product identifier.
        """
        unique_ids = tuple(dict.fromkeys(product_ids))
        result = {product_id: False for product_id in unique_ids}
        if not unique_ids:
            return result

        placeholders = ", ".join(["%s"] * len(unique_ids))
        sql = f"""
            SELECT pc.product_id, c.courier, cp.level
            FROM oc_product_to_category AS pc
            INNER JOIN oc_category_path AS cp
                ON cp.category_id = pc.category_id
            INNER JOIN oc_category AS c
                ON c.category_id = cp.path_id
            WHERE pc.product_id IN ({placeholders}) AND c.courier = 1
            ORDER BY pc.product_id ASC, cp.level DESC
        """
        with get_connection(self.COUNTRY).cursor() as cursor:
            cursor.execute(sql, unique_ids)
            rows = cursor.fetchall()

        seen: set[int] = set()
        for row in rows:
            product_id = int(row["product_id"])
            if product_id in seen or product_id not in result:
                continue
            result[product_id] = bool(row["courier"])
            seen.add(product_id)
        return result

    def save_unknown_unspsc(self, unspsc: str) -> None:
        """Insert an unknown UNSPSC idempotently.

        Args:
            unspsc: Classification absent from ``oc_arancel_amz``.

        Returns:
            None: ``oc_category_amz_new`` contains the code after the call.
        """
        self.save_unknown_unspsc_many((unspsc,))

    def save_unknown_unspsc_many(self, codes: Sequence[str]) -> None:
        """Insert unknown UNSPSC codes in one round-trip.

        Best-effort only: a failed insert must not abort quotation. The caller
        already falls back to ``oc_setting`` defaults when the code is absent
        from ``oc_arancel_amz``.

        Args:
            codes: Classifications absent from ``oc_arancel_amz``.

        Returns:
            None: ``oc_category_amz_new`` contains the codes when the insert
                succeeds.
        """
        unique_codes = tuple(dict.fromkeys(code for code in codes if code))
        if not unique_codes:
            return
        sql = """
            INSERT IGNORE INTO oc_category_amz_new (category_amz)
            VALUES (%s)
        """
        try:
            with get_connection(self.COUNTRY).cursor() as cursor:
                cursor.executemany(sql, [(code,) for code in unique_codes])
        except Exception as exc:
            logger.warning(
                "Could not record unknown UNSPSC codes for %s (%s): %s",
                self.COUNTRY,
                ", ".join(unique_codes),
                exc,
            )

    @staticmethod
    def _unspsc_from_row(
        row: dict,
        settings: CountrySettingsDTO,
    ) -> UnspscDataDTO:
        """Map one ``oc_arancel_amz`` row, filling nulls from settings.

        Args:
            row: Database record for a known UNSPSC.
            settings: Country defaults loaded from ``oc_setting``.

        Returns:
            UnspscDataDTO: Complete policy for the code.
        """
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
