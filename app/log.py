# -*- coding: utf-8 -*-
"""Loglash — konsol + (ixtiyoriy) aylanuvchi fayl (LOG_FILE, LOG_LEVEL).

(Ilgari `common.py` da — ko'chirilgan.)
"""

import io
import logging
import logging.handlers
import os
import sys

from app import config


_LOGGER = None


def _utf8_stdout():
    """Windows konsolida kirillcha buzilmasligi uchun."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        else:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                          errors="replace")
    except Exception:
        pass


def setup_logging(nom="tender"):
    """Konsol + (ixtiyoriy) aylanuvchi fayl logi. Bir marta sozlanadi."""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    _utf8_stdout()

    lg = logging.getLogger(nom)
    lg.setLevel(getattr(logging, config.hozir_log_level().upper(), logging.INFO))
    lg.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    lg.addHandler(sh)

    log_file = config.hozir_log_file()
    if log_file:
        log_file = config.yol(log_file, log_file)
        try:
            os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            lg.addHandler(fh)
        except OSError as exc:
            lg.warning("Log faylini ochib bo'lmadi (%s): %s", log_file, exc)

    _LOGGER = lg
    return lg


def log(msg, level="info"):
    """Loyihaning barcha joyida ishlatiladigan sodda log funksiyasi."""
    lg = setup_logging()
    getattr(lg, level, lg.info)(msg)
