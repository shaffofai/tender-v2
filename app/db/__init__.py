# -*- coding: utf-8 -*-
"""Baza: ulanish manzili, qat'iy sxema (`schema`), migratsiyalar (`migrate`)."""

import re

from app import config

DATABASE_URL = config.baza().database_url


def safe_dsn():
    """Parolsiz ulanish satri — loglar va ekran uchun."""
    return re.sub(r"://[^@]*@", "://***@", DATABASE_URL)
