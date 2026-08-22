import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from app.config import get_settings
from app.nodes import get_registry_count, get_registry_fingerprint
from app.nodes.audit import require_published_nodes_available
from app.security.crypto import key_ring_fingerprint, validate_key_ring
from app.database import SessionLocal
from app.temporal.activities import ACTIVITIES
from app.temporal.client import get_temporal_client
from app.temporal.workflows import GenericFlowWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("workflow-temporal-worker")


async def main() -> None:
    settings = get_settings()
    validate_key_ring(settings)
    with SessionLocal() as db:
        require_published_nodes_available(db)
    logger.info(
        "Temporal worker started task_queue=%s node_registry_count=%s "
        "node_registry_fingerprint=%s credential_key_ring=%s",
        settings.temporal_task_queue,
        get_registry_count(),
        get_registry_fingerprint(),
        key_ring_fingerprint(settings),
    )
    client = await get_temporal_client()
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[GenericFlowWorkflow],
        activities=ACTIVITIES,
        activity_executor=ThreadPoolExecutor(max_workers=20),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
