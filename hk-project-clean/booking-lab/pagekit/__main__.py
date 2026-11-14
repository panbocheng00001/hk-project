# -*- coding: utf-8 -*-
"""``python -m pagekit`` 的入口，只做转发。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
