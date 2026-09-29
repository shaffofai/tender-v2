# -*- coding: utf-8 -*-
"""Basic Auth — KIRUVCHI_LOGIN / KIRUVCHI_PAROL (kalitlarni BIZ beramiz).

(Ilgari `api_server.py` da — ko'chirilgan.)
"""

import base64
import hmac

from app import config

KIRUVCHI_LOGIN = config.api().kiruvchi_login
KIRUVCHI_PAROL = config.api().kiruvchi_parol


def _auth_ok(request):
    """Basic Auth tekshiruvi — vaqt bo'yicha barqaror solishtirish bilan."""
    if not KIRUVCHI_LOGIN or not KIRUVCHI_PAROL:
        return False, "server sozlanmagan: KIRUVCHI_LOGIN/KIRUVCHI_PAROL yo'q"
    sarlavha = request.headers.get("authorization", "")
    if not sarlavha.lower().startswith("basic "):
        return False, "Basic Auth kerak"
    try:
        xom = base64.b64decode(sarlavha[6:].strip(), validate=True)
        login, _, parol = xom.decode("utf-8", "replace").partition(":")
    except Exception:
        return False, "Authorization sarlavhasi buzuq"
    # `hmac.compare_digest` — login/parol uzunligi bo'yicha sirni oshkor qilmaydi
    mos = (hmac.compare_digest(login, KIRUVCHI_LOGIN)
           and hmac.compare_digest(parol, KIRUVCHI_PAROL))
    return (True, "") if mos else (False, "login yoki parol noto'g'ri")
