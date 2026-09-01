"""Shared commercial rounding used by every country calculator."""

from decimal import ROUND_CEILING, Decimal

from DTO.quotation_context_dto import CountrySettingsDTO
from Utils.exceptions import ProductQuotationError


def round_price(value: Decimal, settings: CountrySettingsDTO) -> Decimal:
    """Round a local price up to the next multiple of the configured step.

    Args:
        value: Unrounded local sale price.
        settings: Must include a positive ``price_rounding_step``.

    Returns:
        Decimal: Price rounded toward positive infinity to the step.
    """
    step = settings.decimal("price_rounding_step")
    if step <= 0:
        raise ProductQuotationError("price_rounding_step must be greater than zero.")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step
