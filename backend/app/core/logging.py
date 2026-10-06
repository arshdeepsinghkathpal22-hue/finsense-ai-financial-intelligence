"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"(?i)(password|passwd|api[_-]?key|token|secret|authorization)(\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)([^@\s]+)(@)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
]


class RedactingFilter(logging.Filter):
    """Removes configured secret values and obvious credential patterns."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _PATTERNS[0].sub(r"\1\2[REDACTED]", text)
        text = _PATTERNS[1].sub(r"\1[REDACTED]\3", text)
        text = _PATTERNS[2].sub("[REDACTED]", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            # Render the traceback now so it can be redacted too.
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def configure_logging(level: str, secrets: list[str]) -> RedactingFilter:
    redactor = RedactingFilter(secrets)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(redactor)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "pdfminer", "pdfplumber", "httpx", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Uvicorn installs its own handlers; make sure they redact as well.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for existing in logging.getLogger(name).handlers:
            existing.addFilter(redactor)
    return redactor
