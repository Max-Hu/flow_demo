from datetime import UTC, datetime

import pytest

from app.scheduling import next_cron_time


def test_step_schedule_returns_next_matching_minute() -> None:
    result = next_cron_time(
        "*/5 * * * *", "UTC", datetime(2026, 8, 9, 12, 2, tzinfo=UTC)
    )

    assert result == datetime(2026, 8, 9, 12, 5, tzinfo=UTC)


def test_schedule_respects_timezone() -> None:
    result = next_cron_time(
        "0 9 * * *", "Asia/Shanghai", datetime(2026, 8, 9, 0, 30, tzinfo=UTC)
    )

    assert result == datetime(2026, 8, 9, 1, 0, tzinfo=UTC)


def test_invalid_schedule_is_rejected() -> None:
    with pytest.raises(ValueError, match="five fields"):
        next_cron_time("every minute", "UTC")
