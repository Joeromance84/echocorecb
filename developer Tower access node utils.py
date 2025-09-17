import json
import logging
import sys
import time
import uuid
from typing import Any, Dict, Optional
from src.common.config import get_config

def get_logger(name: Optional[str] = None, level: Optional[int] = None) -> logging.Logger:
    """
    Create and return a standardized logger instance.
    Logs to stdout in a consistent format. Log level can be set via config or argument.
    """
    logger = logging.getLogger(name or __name__)
    if not logger.handlers:
        config = get_config()
        log_level = level or config.get("logging", {}).get("level", logging.INFO)
        if isinstance(log_level, str):
            log_level = getattr(logging, log_level.upper(), logging.INFO)
        
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(log_level)
    return logger

def to_json(data: Any, indent: Optional[int] = None) -> str:
    """
    Safely convert Python data to a JSON string with sorted keys.
    """
    try:
        return json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as e:
        raise ValueError(f"Failed to serialize to JSON: {e}") from e

def from_json(data: str) -> Any:
    """
    Safely parse JSON into Python objects.
    """
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"Failed to parse JSON: {e}") from e

def generate_uuid() -> str:
    """
    Generate a random UUID4 string.
    """
    return str(uuid.uuid4())

def current_timestamp() -> int:
    """
    Get the current Unix timestamp in seconds.
    """
    return int(time.time())

def current_timestamp_ms() -> int:
    """
    Get the current Unix timestamp in milliseconds.
    """
    return time.time_ns() // 1_000_000

class Timer:
    """
    Context manager for measuring execution time with customizable logging.
    """
    def __init__(self, label: str = "Execution", logger: Optional[logging.Logger] = None):
        self.label = label
        self.logger = logger or get_logger(__name__)
        self.start = None
        self.duration = None

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration = time.perf_counter() - self.start
        self.logger.info(f"{self.label} completed in {self.duration:.4f} seconds.")

    @property
    def elapsed(self) -> float:
        """
        Return the elapsed time in seconds (available after context exit).
        """
        if self.duration is None:
            raise ValueError("Timer has not completed yet.")
        return self.duration