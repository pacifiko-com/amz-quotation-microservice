"""Tests for API Gateway proxy wrapping in the shared Lambda entry."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from DTO.quotation_request_dto import RequestValidationError
from DTO.quotation_response_dto import QuotationResponseDTO
from Utils.lambda_entry import handle_quotation_event


class LambdaEntryTests(unittest.TestCase):
    """Validate direct vs API Gateway response shapes."""

    def test_direct_invoke_returns_unwrapped_payload(self) -> None:
        """Console and SDK invokes keep the public JSON contract at the root."""
        payload = QuotationResponseDTO(success=True, message="ok", result=()).to_dict()
        with patch("Utils.lambda_entry._warm_once"), patch(
            "Utils.lambda_entry.ensure_connection"
        ):
            result = handle_quotation_event(
                {"body": {"country": "GT", "products": [{"product_id": 1}]}},
                lambda event: MagicMock(country="GT"),
                lambda request: QuotationResponseDTO(
                    success=True, message="ok", result=()
                ),
            )
        self.assertEqual(result, payload)
        self.assertNotIn("statusCode", result)

    def test_api_gateway_event_returns_proxy_response(self) -> None:
        """API Gateway proxy integration requires statusCode and a JSON body."""
        with patch("Utils.lambda_entry._warm_once"), patch(
            "Utils.lambda_entry.ensure_connection"
        ):
            result = handle_quotation_event(
                {
                    "body": json.dumps({"country": "GT", "products": [{"product_id": 1}]}),
                    "requestContext": {"stage": "qa"},
                },
                lambda event: MagicMock(country="GT"),
                lambda request: QuotationResponseDTO(
                    success=True, message="ok", result=()
                ),
            )

        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(result["headers"]["Content-Type"], "application/json")
        body = json.loads(result["body"])
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "ok")

    def test_api_gateway_validation_error_uses_http_400(self) -> None:
        """Rejected requests keep the failed JSON payload with HTTP 400."""
        with patch("Utils.lambda_entry._warm_once"):
            result = handle_quotation_event(
                {"body": "{}", "requestContext": {"stage": "qa"}},
                lambda event: (_ for _ in ()).throw(
                    RequestValidationError("bad request")
                ),
                lambda request: QuotationResponseDTO(
                    success=True, message="ok", result=()
                ),
            )
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertFalse(body["success"])
        self.assertEqual(body["message"], "bad request")
        self.assertEqual(body["result"], [])

    def test_warmup_failure_returns_structured_error(self) -> None:
        """Warm-up errors stay inside the public success/message/result shape."""
        with patch(
            "Utils.lambda_entry._warm_once",
            side_effect=ValueError("missing database configuration"),
        ):
            result = handle_quotation_event(
                {"body": {"country": "GT"}},
                lambda event: MagicMock(country="GT"),
                lambda request: QuotationResponseDTO(
                    success=True, message="ok", result=()
                ),
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "missing database configuration")
        self.assertEqual(result["result"], [])
        self.assertNotIn("statusCode", result)


if __name__ == "__main__":
    unittest.main()
