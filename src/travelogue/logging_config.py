"""Logging configuration for the travelogue pipeline.

Usage in modules:
    import logging
    log = logging.getLogger(__name__)
    log.info("Starting stage...")
    log.debug("Processing file: %s", path)
    log.warning("No GPS data for %s", asset_id)
    log.error("Failed to open %s: %s", path, exc)

The root logger is configured once by setup_logging() in the CLI.
All travelogue.* loggers inherit from it.
"""

from __future__ import annotations

import logging
from typing import Literal

from rich.console import Console
from rich.logging import RichHandler

TRAVELOGUE_LOGGER = "travelogue"


def setup_logging(
    verbosity: Literal["quiet", "normal", "verbose"] = "normal",
    console: Console | None = None,
) -> None:
    """Configure the root travelogue logger.

    quiet   → WARNING and above only
    normal  → INFO and above  (default)
    verbose → DEBUG and above (shows per-item detail)
    """
    level_map = {
        "quiet": logging.WARNING,
        "normal": logging.INFO,
        "verbose": logging.DEBUG,
    }
    level = level_map[verbosity]

    handler = RichHandler(
        console=console or Console(stderr=True),
        show_time=False,
        show_path=verbosity == "verbose",
        rich_tracebacks=True,
        markup=True,
        log_time_format="[%H:%M:%S]",
    )
    handler.setLevel(level)

    logger = logging.getLogger(TRAVELOGUE_LOGGER)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the travelogue namespace."""
    return logging.getLogger(f"{TRAVELOGUE_LOGGER}.{name}")
