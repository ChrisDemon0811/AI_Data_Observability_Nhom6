"""Logging for the standalone worker (API imports never create log files)."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

from .settings import PROJECT_ROOT


def configure_logging(log_path=None):
    path = Path(log_path) if log_path is not None else PROJECT_ROOT / "logs" / "scheduler.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("observability")
    if not any(getattr(handler, "_observability_worker", False) for handler in logger.handlers):
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handlers = [logging.StreamHandler(), RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                                                               backupCount=3, encoding="utf-8")]
        for handler in handlers:
            handler.setFormatter(formatter)
            handler._observability_worker = True
            logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
