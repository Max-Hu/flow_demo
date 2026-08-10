import logging
import signal
import time

from app.config import get_settings
from app.database import SessionLocal
from app.nodes import get_registry_count, get_registry_fingerprint
from app.nodes.audit import require_published_nodes_available
from app.security.crypto import key_ring_fingerprint, validate_key_ring
from app.workflow.engine import worker_tick

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("workflow-worker")
settings = get_settings()
running = True


def stop_worker(signum, frame) -> None:
    global running
    running = False


signal.signal(signal.SIGINT, stop_worker)
signal.signal(signal.SIGTERM, stop_worker)


def main() -> None:
    validate_key_ring(settings)
    with SessionLocal() as db:
        require_published_nodes_available(db)
    logger.info(
        "Worker %s started node_registry_count=%s node_registry_fingerprint=%s "
        "credential_key_ring=%s",
        settings.worker_name,
        get_registry_count(),
        get_registry_fingerprint(),
        key_ring_fingerprint(settings),
    )
    while running:
        try:
            worked = worker_tick(settings.worker_name)
        except Exception:  # noqa: BLE001 - keep the durable worker alive
            logger.exception("Worker tick failed")
            worked = False
        if not worked:
            time.sleep(settings.worker_poll_seconds)
    logger.info("Worker %s stopped", settings.worker_name)


if __name__ == "__main__":
    main()
