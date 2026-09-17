"""Closed-form Monday-Friday day counts."""

from datetime import date


def weekdays_between(today: date, target: date) -> int:
    """Count Monday-Friday dates in ``(today, target]``.

    Args:
        today: Exclusive start date.
        target: Inclusive end date.

    Returns:
        int: Business days after ``today`` through ``target``. Zero when
            ``target`` is on or before ``today``.
    """
    if target <= today:
        return 0
    return _weekdays_upto(target.toordinal()) - _weekdays_upto(today.toordinal())


def _weekdays_upto(ordinal: int) -> int:
    """Count Monday-Friday dates with ordinal in ``1..ordinal``.

    Python's ``date.toordinal()`` starts at Monday for day 1.
    """
    weeks, extra = divmod(ordinal, 7)
    return weeks * 5 + min(extra, 5)
