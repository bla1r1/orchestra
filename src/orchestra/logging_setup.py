"""JSON-lines structured logging. One record per attempt/decision, appended to
logs/orchestra.log, so fallbacks are auditable after the fact."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

_STD = set(logging.makeLogRecord({}).__dict__)  # baseline attrs to skip


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                payload[k] = v
        return json.dumps(payload, default=str)


def setup_logging(root: Path, *, verbose: bool = False) -> None:
    logger = logging.getLogger("orchestra")
    if logger.handlers:  # idempotent
        return
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "orchestra.log")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    if verbose:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(stream)
