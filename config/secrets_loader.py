"""Load database settings from AWS Secrets Manager into the process environment."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_secrets_applied = False


def apply_secrets_from_manager() -> None:
    """Copy JSON secret keys into ``os.environ`` once per execution environment.

    Existing non-empty environment variables are preserved so local ``.env``
    values and explicit Lambda configuration win over the secret. The AWS
    Lambda Python runtime provides boto3; it is imported lazily so unit tests
    without an ARN do not require the package.
    """
    global _secrets_applied
    if _secrets_applied:
        return
    _secrets_applied = True

    secret_arn = (os.getenv("SECRETS_MANAGER_SECRET_ARN") or "").strip()
    if not secret_arn:
        return

    try:
        import boto3
    except ImportError as exc:
        raise ValueError(
            "boto3 is required to load SECRETS_MANAGER_SECRET_ARN."
        ) from exc

    try:
        response = boto3.client("secretsmanager").get_secret_value(
            SecretId=secret_arn
        )
        payload = _parse_secret_string(response.get("SecretString"))
    except Exception as exc:
        raise ValueError(
            "Could not load database configuration from Secrets Manager."
        ) from exc

    applied = 0
    for key, value in payload.items():
        if not key or os.environ.get(key):
            continue
        os.environ[key] = str(value)
        applied += 1
    logger.info("Applied %s keys from Secrets Manager.", applied)


def reset_secrets_loader() -> None:
    """Allow tests to run another Secrets Manager load in the same process."""
    global _secrets_applied
    _secrets_applied = False


def _parse_secret_string(secret_string: Any) -> dict[str, Any]:
    """Parse a Secrets Manager ``SecretString`` as a JSON object.

    Args:
        secret_string: Raw secret payload returned by GetSecretValue.

    Returns:
        dict[str, Any]: Key/value pairs to merge into the environment.

    Raises:
        ValueError: If the payload is missing or is not a JSON object.
    """
    if not isinstance(secret_string, str) or not secret_string.strip():
        raise ValueError("Secrets Manager SecretString must be a JSON object.")
    try:
        parsed = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError("Secrets Manager SecretString must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Secrets Manager SecretString must be a JSON object.")
    return parsed
