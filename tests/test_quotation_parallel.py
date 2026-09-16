"""Tests for worker sizing and parallel product quoting."""

from __future__ import annotations

import os
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config.secrets_loader import reset_secrets_loader
from config.settings import get_settings, quote_worker_count
from country.country_prefetched_strategy import CountryPrefetchedStrategy
from DTO.quotation_context_dto import (
    CountrySettingsDTO,
    ProductDataDTO,
    QuotationLookupDTO,
    SalesIvaDTO,
    UnspscDataDTO,
)
from DTO.quotation_request_dto import ProductFactsDTO
from DTO.quotation_response_dto import ResultObjectDTO
from service.quotation_orchestrator import QuotationOrchestrator
from Utils.exceptions import QuotationError


def _worker_settings(
    threads: int = 10,
    min_products: int = 10,
) -> SimpleNamespace:
    """Return the worker knobs consumed by the orchestrator."""
    return SimpleNamespace(
        quotation_worker_threads=threads,
        quotation_min_products_per_worker=min_products,
    )


def _settings() -> CountrySettingsDTO:
    """Return enough oc_setting keys for policy resolution."""
    return CountrySettingsDTO(
        {
            "margen": "1.1",
            "max_product_weight_kg": "100",
            "default_iva_venta": "0.12",
            "default_arancel": "0.15",
            "default_restriction": "0",
            "default_category_code": "0",
            "default_danger_good_active": "0",
            "default_courier": "0",
        }
    )


def _unspsc() -> UnspscDataDTO:
    """Return a stored UNSPSC policy used by memory-only lookups."""
    return UnspscDataDTO(
        arancel_percentage=Decimal("0.15"),
        restriction=0,
        category_code=0,
        margin_percentage=Decimal("1.1"),
        danger_good_active=False,
        courier=False,
    )


def _lookup(product_ids: tuple[int, ...], codes: tuple[str, ...]) -> QuotationLookupDTO:
    """Build an in-memory prefetch map for the given products."""
    return QuotationLookupDTO(
        products={
            product_id: ProductDataDTO(
                weight_lb=Decimal("1"),
                courier=False,
                partida=None,
            )
            for product_id in product_ids
        },
        unspsc={code: _unspsc() for code in codes},
        tariffs={},
        category_courier={product_id: False for product_id in product_ids},
        sales_iva={},
    )


def _product(product_id: int, unspsc: str = "11111111") -> ProductFactsDTO:
    """Create a minimal product used by the parallel loop."""
    return ProductFactsDTO(
        product_id=product_id,
        amz_weight_kg=Decimal("0.5"),
        unspsc=unspsc,
    )


def _ok_result(product_id: int) -> ResultObjectDTO:
    """Build a successful product result."""
    return ResultObjectDTO(success=True, message="ok", product_id=product_id)


def _bound_strategy(lookup: QuotationLookupDTO | None = None) -> CountryPrefetchedStrategy:
    """Wrap a mocked inner Strategy that fails if quote_product queries."""
    inner = MagicMock()
    inner.resolve_settings.return_value = _settings()
    inner.resolve_exchange_rate.return_value = Decimal("7.8")
    inner.get_default_unspsc.return_value = _unspsc()
    inner.resolve_sales_iva_rate.return_value = SalesIvaDTO(
        rate=Decimal("0.12"),
        quotation_notes=(),
    )
    inner.get_unspsc_data.side_effect = AssertionError(
        "quote_product must not query UNSPSC after bind."
    )
    inner.get_product_data.side_effect = AssertionError(
        "quote_product must not query oc_product after bind."
    )
    inner.resolve_tariff_data.side_effect = AssertionError(
        "quote_product must not query tariffs after bind."
    )
    inner.get_category_tree_courier.side_effect = AssertionError(
        "quote_product must not query category tree after bind."
    )
    inner.save_unknown_unspsc.side_effect = AssertionError(
        "quote_product must not insert unknown UNSPSC after bind."
    )
    return CountryPrefetchedStrategy(inner, lookup)


class QuoteWorkerCountTests(unittest.TestCase):
    """Verify thread count shrinks when the batch is too small."""

    def test_defaults_keep_small_batches_on_one_worker(self) -> None:
        """Three products stay sequential with the default minimum of 10."""
        self.assertEqual(quote_worker_count(3, 10, 10), 1)

    def test_workers_shrink_to_available_chunks(self) -> None:
        """25 products with min 10 and cap 10 start two workers."""
        self.assertEqual(quote_worker_count(25, 10, 10), 2)

    def test_workers_never_exceed_the_configured_cap(self) -> None:
        """100 products with min 10 stay at the thread cap."""
        self.assertEqual(quote_worker_count(100, 10, 10), 10)

    def test_workers_never_exceed_the_product_count(self) -> None:
        """A tiny minimum still cannot start more threads than products."""
        self.assertEqual(quote_worker_count(4, 10, 1), 4)

    def test_empty_or_single_product_uses_one_worker(self) -> None:
        """Zero or one product never starts a pool."""
        self.assertEqual(quote_worker_count(0, 10, 10), 1)
        self.assertEqual(quote_worker_count(1, 10, 10), 1)


class WorkerSettingsTests(unittest.TestCase):
    """Verify Secrets Manager / environment knobs and their defaults."""

    def tearDown(self) -> None:
        """Drop cached settings between cases."""
        get_settings.cache_clear()
        reset_secrets_loader()

    def test_worker_settings_default_to_ten(self) -> None:
        """Missing secrets fall back to 10 threads and 10 products each."""
        environment = {
            "DB_GT_HOST": "gt-host",
            "DB_GT_NAME": "gt-db",
            "DB_GT_USER": "gt-user",
            "DB_CR_HOST": "cr-host",
            "DB_CR_NAME": "cr-db",
            "DB_CR_USER": "cr-user",
        }
        with patch.dict(os.environ, environment, clear=True):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.quotation_worker_threads, 10)
        self.assertEqual(settings.quotation_min_products_per_worker, 10)

    def test_invalid_worker_settings_use_defaults(self) -> None:
        """Zero, negative or non-numeric values keep the defaults."""
        environment = {
            "DB_GT_HOST": "gt-host",
            "DB_GT_NAME": "gt-db",
            "DB_GT_USER": "gt-user",
            "DB_CR_HOST": "cr-host",
            "DB_CR_NAME": "cr-db",
            "DB_CR_USER": "cr-user",
            "QUOTATION_WORKER_THREADS": "0",
            "QUOTATION_MIN_PRODUCTS_PER_WORKER": "nope",
        }
        with patch.dict(os.environ, environment, clear=True):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.quotation_worker_threads, 10)
        self.assertEqual(settings.quotation_min_products_per_worker, 10)


class QuotationParallelTests(unittest.TestCase):
    """Verify order, error isolation and memory-only workers."""

    def _execute(
        self,
        products: tuple[ProductFactsDTO, ...],
        quote_product,
        *,
        threads: int = 10,
        min_products: int = 1,
        lookup: QuotationLookupDTO | None = None,
    ):
        """Run execute with a bound Strategy and patched worker settings."""
        strategy = _bound_strategy()
        orchestrator = QuotationOrchestrator()
        prefetch = lookup or _lookup(
            tuple(item.product_id for item in products),
            tuple(item.unspsc for item in products if item.unspsc),
        )
        with (
            patch(
                "service.quotation_orchestrator.CountryStrategyFactory.create",
                return_value=strategy,
            ),
            patch.object(orchestrator, "prefetch", return_value=prefetch),
            patch(
                "service.quotation_orchestrator.get_settings",
                return_value=_worker_settings(threads, min_products),
            ),
        ):
            return orchestrator.execute("GT", products, quote_product), strategy

    def test_small_batch_does_not_start_a_thread_pool(self) -> None:
        """Default min products per worker keeps a 3-product request sequential."""
        products = tuple(_product(index) for index in range(1, 4))

        def quote_product(strategy, product, settings):
            return _ok_result(product.product_id)

        with patch(
            "service.quotation_orchestrator.ThreadPoolExecutor"
        ) as pool:
            self._execute(
                products,
                quote_product,
                threads=10,
                min_products=10,
            )

        pool.assert_not_called()

    def test_parallel_results_keep_request_order(self) -> None:
        """Slower early products still occupy the original result slots."""
        products = tuple(_product(index) for index in range(1, 26))
        seen_workers: set[int] = set()

        def quote_product(strategy, product, settings):
            seen_workers.add(id(strategy))
            time.sleep((26 - product.product_id) * 0.002)
            return _ok_result(product.product_id)

        response, strategy = self._execute(
            products,
            quote_product,
            threads=10,
            min_products=10,
        )

        self.assertEqual(
            [item.product_id for item in response.result],
            [item.product_id for item in products],
        )
        self.assertTrue(all(item.success for item in response.result))
        self.assertTrue(response.success)
        self.assertEqual(seen_workers, {id(strategy)})

    def test_parallel_isolates_one_product_error(self) -> None:
        """One rejected product does not stop the rest of the batch."""
        products = tuple(_product(index) for index in range(1, 21))

        def quote_product(strategy, product, settings):
            if product.product_id == 7:
                raise QuotationError("isolated product failure")
            return _ok_result(product.product_id)

        response, _strategy = self._execute(
            products,
            quote_product,
            threads=10,
            min_products=10,
        )

        self.assertFalse(response.success)
        self.assertEqual(len(response.result), 20)
        failed = response.result[6]
        self.assertFalse(failed.success)
        self.assertEqual(failed.product_id, 7)
        self.assertEqual(failed.message, "isolated product failure")
        self.assertEqual(
            [item.product_id for item in response.result if item.success],
            [item.product_id for item in products if item.product_id != 7],
        )

    def test_quote_loop_does_not_call_get_connection(self) -> None:
        """Workers resolve policy from the bound map and never open MySQL."""
        products = (
            _product(1, "11111111"),
            _product(2, "missing-code"),
            _product(3, "11111111"),
        )
        lookup = _lookup((1, 2, 3), ("11111111",))
        connection_error = AssertionError(
            "get_connection must not run inside the quote loop."
        )

        def quote_product(strategy, product, settings):
            policy = QuotationOrchestrator().resolve_policy(
                strategy, product, settings
            )
            strategy.resolve_exchange_rate(settings)
            return ResultObjectDTO(
                success=True,
                message="ok",
                product_id=product.product_id,
                courier=policy.courier,
            )

        with (
            patch(
                "database.connection.get_connection",
                side_effect=connection_error,
            ),
            patch(
                "repository.base_quotation_repository.get_connection",
                side_effect=connection_error,
            ),
            patch(
                "country.GT.repository.gt_quotation_repository.get_connection",
                side_effect=connection_error,
            ),
            patch(
                "country.CR.repository.cr_quotation_repository.get_connection",
                side_effect=connection_error,
            ),
        ):
            response, strategy = self._execute(
                products,
                quote_product,
                threads=10,
                min_products=1,
                lookup=lookup,
            )

        self.assertTrue(response.success)
        self.assertEqual(len(response.result), 3)
        strategy._inner.get_unspsc_data.assert_not_called()
        strategy._inner.get_product_data.assert_not_called()
        strategy._inner.save_unknown_unspsc.assert_not_called()
        strategy._inner.load_products.assert_not_called()

    def test_bound_strategy_returns_none_for_missing_unspsc(self) -> None:
        """A prefetch miss stays in memory instead of querying the inner Strategy."""
        strategy = _bound_strategy(_lookup((1,), ()))

        result = strategy.get_unspsc_data("missing-code", _settings())

        self.assertIsNone(result)
        strategy._inner.get_unspsc_data.assert_not_called()

    def test_bound_strategy_refuses_batch_queries(self) -> None:
        """Prefetch helpers cannot reopen MySQL after the map is bound."""
        strategy = _bound_strategy(_lookup((1,), ("11111111",)))

        with self.assertRaisesRegex(RuntimeError, "forbidden after the prefetch"):
            strategy.load_products((1,))


if __name__ == "__main__":
    unittest.main()
