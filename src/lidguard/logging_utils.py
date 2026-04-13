from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging for CLI execution."""
    normalized = level.upper()
    logging.basicConfig(
        level=getattr(logging, normalized, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

