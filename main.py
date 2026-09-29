# -*- coding: utf-8 -*-
"""main.py — tekshiruv worker'i (ishlab chiqarish sikli: `--davomiy -y`).

Kod: `app/worker/run.py`.  Yordam: python main.py --help
"""
import sys

from app.worker.run import main

if __name__ == "__main__":
    sys.exit(main())
