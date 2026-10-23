from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def build_time_range(
    date_value: str | None,
    from_value: str | None,
    to_value: str | None,
    timezone_name: str,
) -> tuple[datetime | None, datetime | None]:
    if date_value and (from_value or to_value):
        raise ValueError("use either date or from/to, not both")

    timezone = ZoneInfo(timezone_name)
    if date_value:
        try:
            start = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=timezone)
        except ValueError:
            raise ValueError(f"invalid date {date_value!r}; expected YYYY-MM-DD") from None
        return start, start + timedelta(days=1) - timedelta(seconds=1)

    start = parse_local_datetime(from_value, timezone) if from_value else None
    end = parse_local_datetime(to_value, timezone) if to_value else None
    if start and end and start > end:
        raise ValueError("from must be before or equal to to")
    return start, end


def parse_local_datetime(value: str, timezone: ZoneInfo) -> datetime:
    """Parse "YYYY-MM-DD HH:MM" as local time, or ISO 8601 with an offset.

    A value with an offset (such as "2026-07-20T12:00:00Z") is converted to the
    local timezone. A date without a time is rejected: as an end bound it would mean
    midnight and silently drop the rest of that day.
    """
    text = value.strip()
    try:
        if "T" not in text and " " not in text:
            raise ValueError("missing time")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"invalid datetime {value!r}; expected YYYY-MM-DD HH:MM or ISO 8601 with a time"
        ) from None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)
