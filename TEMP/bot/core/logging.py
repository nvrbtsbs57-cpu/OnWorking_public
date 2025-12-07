import json
import logging
import sys
from typing import Optional


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "time": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        return json.dumps(log_record, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_mode: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Clear existing handlers
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)

    if json_mode:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
        )
    handler.setFormatter(formatter)
    root.addHandler(handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name)
