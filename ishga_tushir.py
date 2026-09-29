# -*- coding: utf-8 -*-
"""ishga_tushir.py — qo'lda ishga tushirish (menyu, --holat, --quruq ...).

Kod: `app/tools/ishga_tushir.py`.  Yordam: python ishga_tushir.py --help
"""
import sys

from app.tools.ishga_tushir import main

if __name__ == "__main__":
    sys.exit(main() or 0)
