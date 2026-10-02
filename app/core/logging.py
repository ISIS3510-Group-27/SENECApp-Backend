import logging


def setup_logging(level: int = logging.INFO) -> None:
    """Show the application's own log messages (``app.*``) next to Uvicorn's."""
    logger = logging.getLogger("app")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)-5s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
