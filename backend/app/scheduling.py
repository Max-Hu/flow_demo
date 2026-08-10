from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.enums import FlowStatus, RunTriggerType
from app.flow_config import deep_merge
from app.models import FlowSchedule, utc_now
from app.run_service import create_flow_run

logger = logging.getLogger(__name__)


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


def scheduler_tick() -> int:
    now = utc_now()
    created = 0
    with SessionLocal() as db:
        schedules = db.scalars(
            select(FlowSchedule)
            .where(FlowSchedule.enabled.is_(True), FlowSchedule.next_run_at <= now)
            .options(selectinload(FlowSchedule.flow), selectinload(FlowSchedule.flow_version))
            .order_by(FlowSchedule.next_run_at)
            .with_for_update(skip_locked=True)
            .limit(25)
        ).all()
        for schedule in schedules:
            due_at = schedule.next_run_at
            schedule.next_run_at = next_cron_time(
                schedule.cron_expression, schedule.timezone, due_at
            )
            schedule.updated_at = now
            if schedule.flow.status != FlowStatus.ACTIVE:
                continue
            create_flow_run(
                db,
                schedule.flow,
                schedule.flow_version,
                schedule.input_data,
                trigger_type=RunTriggerType.SCHEDULE,
                trigger_id=schedule.id,
                idempotency_key=f"schedule:{schedule.id}:{due_at.isoformat()}",
                source_metadata={
                    "scheduleName": schedule.name,
                    "scheduledFor": due_at.isoformat(),
                    "cronExpression": schedule.cron_expression,
                    "timezone": schedule.timezone,
                },
                flow_config=deep_merge(
                    schedule.flow_version.default_config, schedule.config_overrides
                ),
            )
            schedule.last_triggered_at = now
            created += 1
        db.commit()
    return created


def validate_schedule(expression: str, timezone: str) -> datetime:
    return next_cron_time(expression, timezone)
