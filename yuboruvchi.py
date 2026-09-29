# -*- coding: utf-8 -*-
"""yuboruvchi.py — verdiktlarni tender tizimiga yuboruvchi (`--davomiy`).

Kod: `app/sender/`.  Yordam: python yuboruvchi.py --help
"""
import sys

from app.sender.cli import main

if __name__ == "__main__":
    sys.exit(main())
