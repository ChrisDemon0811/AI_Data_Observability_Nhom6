"""Standalone APScheduler 3.x worker; never started by FastAPI."""
import argparse
from datetime import datetime, timezone
import logging
import os
import signal

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from .logging_config import configure_logging
from .service import run_pipeline
from .settings import PROJECT_ROOT
from .worker_lock import WorkerAlreadyRunning, single_worker

logger = logging.getLogger(__name__)


def scheduled_run():
    try:
        return run_pipeline("normal")
    except Exception:
        # run_pipeline logs the traceback; keep the next interval eligible.
        logger.error("Scheduled attempt failed; next interval will continue")
        return None


def create_scheduler(interval_seconds=300):
    if interval_seconds <= 0:
        raise ValueError("Scheduler interval must be positive")
    scheduler = BlockingScheduler(timezone=timezone.utc,
                                  executors={"default": ThreadPoolExecutor(max_workers=1)})
    scheduler.add_job(scheduled_run, "interval", seconds=interval_seconds,
                      id="normal_pipeline", max_instances=1, coalesce=True,
                      misfire_grace_time=max(1, int(interval_seconds)),
                      next_run_time=datetime.now(timezone.utc))
    return scheduler


def _stop(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Standalone APScheduler pipeline worker")
    parser.add_argument("--interval", type=int, default=os.getenv("SCHEDULER_INTERVAL_SECONDS", "300"))
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    try:
        with single_worker(PROJECT_ROOT / "data" / "scheduler.lock"):
            configure_logging()
            scheduler = create_scheduler(args.interval)
            previous_handlers = {}
            for sig in (signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGINT)):
                previous_handlers[sig] = signal.signal(sig, _stop)
            logger.info("Worker starting pid=%s interval_seconds=%s first_run=immediate", os.getpid(), args.interval)
            try:
                scheduler.start()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Worker stopping; waiting for active pipeline to finish")
            finally:
                if scheduler.running:
                    scheduler.shutdown(wait=True)
                for sig, previous in previous_handlers.items():
                    signal.signal(sig, previous)
                logger.info("Worker stopped")
    except WorkerAlreadyRunning as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
