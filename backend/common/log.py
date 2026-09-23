"""Structured JSON logs (plan.md §4): the log message itself is one JSON object."""

import json
import logging
import re


def jlog(logger: logging.Logger, log_level: int, event: str, /, **fields) -> None:
    logger.log(log_level, json.dumps({"event": event, **fields}, default=str, sort_keys=True))


_SECRET_PARAM = re.compile(r"((?:token|access_token)=)[^&\s\"']+")


class RedactSecrets(logging.Filter):
    """Masks `token=` query values in log lines. The WebSocket JWT travels in the URL
    (browsers can't set headers on a WebSocket), and uvicorn logs that URL on every
    connect — without this, every operator/supervisor token lands in the container logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_PARAM.sub(r"\1***", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def install_redaction(*logger_names: str) -> None:
    for name in logger_names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecrets) for f in logger.filters):
            logger.addFilter(RedactSecrets())
