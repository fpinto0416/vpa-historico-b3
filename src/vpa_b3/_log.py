"""Logs estruturados (uma linha JSON por evento)."""
from __future__ import annotations

import json
import logging
import sys
import time


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        evento = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "nivel": record.levelname,
            "modulo": record.name,
            "msg": record.getMessage(),
        }
        evento.update(getattr(record, "ctx", {}))
        return json.dumps(evento, ensure_ascii=False, default=str)


def get_logger(nome: str) -> logging.Logger:
    logger = logging.getLogger(nome)
    if not logging.getLogger().handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(_JsonFormatter())
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.INFO)
        for ruidoso in ("httpx", "yfinance", "peewee", "urllib3"):
            logging.getLogger(ruidoso).setLevel(logging.WARNING)
    return logger


def log(logger: logging.Logger, msg: str, nivel: int = logging.INFO, **ctx) -> None:
    logger.log(nivel, msg, extra={"ctx": ctx})
