import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_URL_QUERY_RE = re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|auth[_-]?token|access[_-]?token|password)"
    r"(\s*[:=]\s*)[^\s,;]+"
)
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(Bearer\s+|Token\s+token=)[A-Za-z0-9._~+/=-]+")


def redact_log_text(value: object) -> str:
    """Remove credentials and signed URL queries before text reaches a log sink."""
    text = str(value)
    text = _AUTH_SCHEME_RE.sub(r"\1[redacted]", text)
    text = _URL_QUERY_RE.sub(r"\1?[redacted]", text)
    return _SECRET_RE.sub(r"\1\2[redacted]", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def configure_logging(config_dir=None):
    """Configure application-wide logging. Call once at startup.

    Sets up a RotatingFileHandler and a StreamHandler on the root logger
    so that every module using ``logging.getLogger(__name__)`` automatically
    inherits the same handlers and format.
    """
    log_dir = Path(config_dir or os.getenv("MYFANS_DATA_DIR", ".runtime")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "myfans_downloader.log"

    formatter = RedactingFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_file, maxBytes=10485760, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Clear existing handlers to avoid duplicates on repeated calls
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
