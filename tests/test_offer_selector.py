"""Tests for raw Amazon offer normalization and selection."""

from __future__ import annotations

import unittest
from decimal import Decimal

from DTO.quotation_context_dto import CountrySettingsDTO
from service.offer_selector import OfferSelector
from Utils.exceptions import ProductQuotationError


class OfferSelectorTests(unittest.TestCase):
    """Verify configured eligibility and fulfillment shipping rules."""

    def setUp(self) -> None:
        """Load the shared GT-based selector settings."""
        self.settings = CountrySettingsDTO(
            {
                "amazon_new_condition": "NEW",
                "allowed_offer_price_types": '["NEW","SALE","BUSINESS","OTHER"]',
                "restricted_guidance_term": "restricted",
                "unavailable_offer_terms": '["out of stock"]',
                "prefer_offer_with_delivery_range": "1",
                "amazon_fulfillment_free_shipping": "1",
                "amazon_fulfillment_type": "AMAZON_FULFILLMENT",
            }
        )

    def test_selects_first_eligible_offer_with_delivery_max(self) -> None:
        """The first eligible offer with deliveryRange.max wins."""
        offers = (
            self._offer("first", delivery=None),
            self._offer("second", delivery="2026-09-10T00:00:00Z"),
        )

        selected = OfferSelector().select(offers, self.settings)

        self.assertEqual(selected.offer_id, "second")
        self.assertEqual(str(selected.price_usd), "100")
        self.assertEqual(str(selected.list_price_usd), "120")

    def test_fallback_uses_first_shipping_option(self) -> None:
        """Without max, shipping comes from shippingOptions[0]."""
        offers = (
            {
                "offerId": "mf-fallback",
                "productCondition": "NEW",
                "price": {"priceType": "NEW", "value": {"amount": 100}},
                "listPrice": {"value": {"amount": 120}},
                "fulfillmentType": "MERCHANT_FULFILLMENT",
                "availability": "In Stock.",
                "buyingGuidance": "",
                "shippingOptions": [
                    {"shippingCost": {"value": {"amount": 5}}},
                    {
                        "shippingCost": {"value": {"amount": 9}},
                        "deliveryRange": {"max": None},
                    },
                ],
            },
        )

        selected = OfferSelector().select(offers, self.settings)

        self.assertEqual(selected.offer_id, "mf-fallback")
        self.assertEqual(selected.shipping_usd, Decimal("5"))
        self.assertEqual(selected.price_usd, Decimal("105"))
        self.assertIsNone(selected.delivery_range_max)

    def test_exact_restricted_guidance_skips_offer(self) -> None:
        """Exact RESTRICTED is a filter; the next eligible offer is used."""
        offers = (
            self._offer("restricted", delivery="2026-09-10T00:00:00Z", guidance="RESTRICTED"),
            self._offer("next", delivery="2026-09-11T00:00:00Z"),
        )

        selected = OfferSelector().select(offers, self.settings)

        self.assertEqual(selected.offer_id, "next")

    def test_contains_restricted_is_not_an_eligibility_filter(self) -> None:
        """A contains match is kept for the post-selection abort."""
        offers = (self._offer("kept", guidance="Export restricted item"),)

        selected = OfferSelector().select(offers, self.settings)

        self.assertEqual(selected.offer_id, "kept")
        self.assertEqual(selected.buying_guidance, "Export restricted item")

    def test_zeroes_amazon_fulfillment_shipping(self) -> None:
        """AF shipping is forced to zero after the option is chosen."""
        selected = OfferSelector().select(
            (self._offer("af", delivery="2026-09-10T00:00:00Z"),),
            self.settings,
        )

        self.assertEqual(selected.shipping_usd, Decimal("0"))
        self.assertEqual(selected.price_usd, Decimal("100"))

    def test_af_first_pass_is_skipped_when_flag_is_off(self) -> None:
        """Without the flag, a merchant offer without max stays first."""
        offers = (
            self._offer("merchant", delivery=None, fulfillment="MERCHANT_FULFILLMENT"),
            self._offer("amazon", delivery=None),
        )

        selected = OfferSelector().select(offers, self.settings)

        self.assertEqual(selected.offer_id, "merchant")

    def test_af_first_pass_selects_amazon_fulfillment(self) -> None:
        """With the flag, the first eligible AF offer wins over merchant."""
        offers = (
            self._offer("merchant", delivery=None, fulfillment="MERCHANT_FULFILLMENT"),
            self._offer("amazon", delivery=None),
        )

        selected = OfferSelector().select(
            offers,
            self.settings,
            prefer_amazon_fulfillment=True,
        )

        self.assertEqual(selected.offer_id, "amazon")
        self.assertEqual(selected.shipping_usd, Decimal("0"))

    def test_delivery_max_still_wins_over_af_first(self) -> None:
        """The max-date pass remains first, even when AF-first is on."""
        offers = (
            self._offer(
                "merchant-max",
                delivery="2026-09-10T00:00:00Z",
                fulfillment="MERCHANT_FULFILLMENT",
            ),
            self._offer("amazon", delivery=None),
        )

        selected = OfferSelector().select(
            offers,
            self.settings,
            prefer_amazon_fulfillment=True,
        )

        self.assertEqual(selected.offer_id, "merchant-max")

    def test_raises_when_no_offer_is_eligible(self) -> None:
        """No fallback offer is invented when every candidate fails."""
        with self.assertRaises(ProductQuotationError):
            OfferSelector().select(
                (self._offer("oos", availability="out of stock"),),
                self.settings,
            )

    @staticmethod
    def _offer(
        offer_id: str,
        delivery: str | None = "2026-09-10T00:00:00Z",
        guidance: str = "",
        availability: str = "In Stock.",
        fulfillment: str = "AMAZON_FULFILLMENT",
    ) -> dict:
        """Build a representative raw OFFERS element."""
        return {
            "offerId": offer_id,
            "productCondition": "NEW",
            "price": {"priceType": "NEW", "value": {"amount": 100}},
            "listPrice": {"value": {"amount": 120}},
            "fulfillmentType": fulfillment,
            "availability": availability,
            "buyingGuidance": guidance,
            "shippingOptions": [
                {
                    "shippingCost": {"value": {"amount": 9}},
                    "deliveryRange": {"max": delivery},
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
