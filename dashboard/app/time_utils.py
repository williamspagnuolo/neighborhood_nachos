from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
UTC_TZ = dt.timezone.utc


def pacific_date_range_to_utc(
    start_date: str | None,
    end_date: str | None,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """
    Convert Pacific date strings into UTC timestamp bounds.

    Returns (start_utc_inclusive, end_utc_exclusive).
    """
    if not start_date or not end_date:
        return None, None

    start_local = dt.datetime.combine(
        dt.date.fromisoformat(start_date),
        dt.time.min,
        tzinfo=PACIFIC_TZ,
    )
    # End date is inclusive in UI. Convert to next-day midnight and use
    # an exclusive upper bound in UTC for consistent filtering.
    end_local_exclusive = dt.datetime.combine(
        dt.date.fromisoformat(end_date) + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=PACIFIC_TZ,
    )
    return start_local.astimezone(UTC_TZ), end_local_exclusive.astimezone(UTC_TZ)


def utc_to_pacific_date(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC_TZ)
    return value.astimezone(PACIFIC_TZ).date().isoformat()
