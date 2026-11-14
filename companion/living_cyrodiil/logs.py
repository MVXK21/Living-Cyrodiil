"""Logging to companion/logs/companion.log (and the terminal for warnings)."""

import logging
import logging.handlers

from .paths import LOG_DIR

_configured = False


def setup_logging(level: str = "INFO", console_level: str = "WARNING") -> None:
    """Log everything at `level` to the file; only warnings and errors to the terminal."""
    global _configured
    if _configured:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "companion.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, console_level.upper(), logging.WARNING))
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root.addHandler(console)

    # The HTTP libraries are chatty at DEBUG and can echo request headers.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
