from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import utc_now


def _cron_values(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        base, separator, step_text = part.partition("/")
        try:
            step = int(step_text) if separator else 1
            if step < 1:
                raise ValueError
            if base == "*":
                start, end = minimum, maximum
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(base)
        except ValueError as exc:
            raise ValueError(f"Invalid cron field: {field}") from exc
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron value outside {minimum}-{maximum}: {part}")
        values.update(range(start, end + 1, step))
    return values


def next_cron_time(expression: str, timezone: str, after: datetime | None = None) -> datetime:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron expression must contain five fields")
    try:
        minutes = _cron_values(fields[0], 0, 59)
        hours = _cron_values(fields[1], 0, 23)
        days = _cron_values(fields[2], 1, 31)
        months = _cron_values(fields[3], 1, 12)
        weekdays = _cron_values(fields[4], 0, 7)
        if 7 in weekdays:
            weekdays.add(0)
    except ValueError as exc:
        raise ValueError(f"Invalid cron expression: {expression}") from exc
    candidate = (after or utc_now()).astimezone(zone).replace(second=0, microsecond=0)
    candidate += timedelta(minutes=1)
    day_is_wildcard = fields[2] == "*"
    weekday_is_wildcard = fields[4] == "*"
    for _ in range(60 * 24 * 366 * 2):
        cron_weekday = (candidate.weekday() + 1) % 7
        day_match = candidate.day in days
        weekday_match = cron_weekday in weekdays
        if not day_is_wildcard and not weekday_is_wildcard:
            calendar_match = day_match or weekday_match
        else:
            calendar_match = day_match and weekday_match
        if (
            candidate.minute in minutes
            and candidate.hour in hours
            and candidate.month in months
            and calendar_match
        ):
            return candidate.astimezone(UTC)
        candidate += timedelta(minutes=1)
    raise ValueError("Cron expression has no occurrence in the next two years")


def validate_schedule(expression: str, timezone: str) -> datetime:
    return next_cron_time(expression, timezone)
