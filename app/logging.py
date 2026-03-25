from __future__ import annotations

import json
import logging
from typing import Any, cast


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = cast(Any, getattr(record, "event", None))
        context = cast(Any, getattr(record, "context", None))
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if event is not None:
            payload["event"] = event
        if context is not None:
            payload["context"] = context
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
