"""Structured JSON logs (plan.md §4): the log message itself is one JSON object."""

import json
import logging


def jlog(logger: logging.Logger, log_level: int, event: str, /, **fields) -> None:
    logger.log(log_level, json.dumps({"event": event, **fields}, default=str, sort_keys=True))
