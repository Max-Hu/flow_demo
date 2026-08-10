import logging
import signal
import time

from app.scheduling import scheduler_tick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("workflow-scheduler")
running = True


def stop_scheduler(signum, frame) -> None:
    global running
    running = False


signal.signal(signal.SIGINT, stop_scheduler)
signal.signal(signal.SIGTERM, stop_scheduler)


def main() -> None:
    logger.info("Scheduler started")
    while running:
        try:
            created = scheduler_tick()
        except Exception:  # noqa: BLE001 - keep the durable scheduler alive
            logger.exception("Scheduler tick failed")
            created = 0
        if not created:
            time.sleep(1)
    logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
