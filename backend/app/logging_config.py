"""JSON logs to stdout (what Render/Railway collect). Only metadata is logged — never keys or page text."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": record.levelname.lower(),
                   "logger": record.name, "msg": record.getMessage()}
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs at INFO; keep it quiet so nothing sensitive is echoed.
    for noisy in ("httpx", "httpcore", "google_genai", "google.genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
