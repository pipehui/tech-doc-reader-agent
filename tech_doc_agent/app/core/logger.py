# tech_doc_agent/app/core/logger.py
import logging
from .settings import get_settings

config = get_settings()

logger = logging.getLogger("tech_doc_agent")
logger.setLevel(getattr(logging, config.LOG_LEVEL))

stream_handler = logging.StreamHandler()
stream_handler.setLevel(getattr(logging, config.LOG_LEVEL))

formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(message)s"
)
stream_handler.setFormatter(formatter)

logger.addHandler(stream_handler)
