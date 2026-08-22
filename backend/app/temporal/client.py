from __future__ import annotations

import asyncio
from functools import lru_cache

from temporalio.client import Client

from app.config import get_settings


@lru_cache
def temporal_settings() -> tuple[str, str, str]:
    settings = get_settings()
    return settings.temporal_address, settings.temporal_namespace, settings.temporal_task_queue


async def get_temporal_client() -> Client:
    address, namespace, _ = temporal_settings()
    last_error: Exception | None = None
    for _ in range(20):
        try:
            return await Client.connect(address, namespace=namespace)
        except Exception as exc:  # noqa: BLE001 - startup waits for Temporal readiness
            last_error = exc
            await asyncio.sleep(1)
    assert last_error is not None
    raise last_error
