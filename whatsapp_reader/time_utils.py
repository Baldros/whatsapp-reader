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
        start = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=timezone)
        return start, start + timedelta(days=1) - timedelta(seconds=1)

    start = parse_local_datetime(from_value, timezone) if from_value else None
    end = parse_local_datetime(to_value, timezone) if to_value else None
    if start and end and start > end:
        raise ValueError("from must be before or equal to to")
    return start, end


def parse_local_datetime(value: str, timezone: ZoneInfo) -> datetime:
    normalized = value.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=timezone)
        except ValueError:
            pass
    raise ValueError(f"invalid datetime {value!r}; expected YYYY-MM-DD HH:MM")
