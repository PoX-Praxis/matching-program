#!/usr/bin/env python3
"""
PoX v4 スキーマ初期化 CLI（仕様書 B章 / Step 1 DoD）

使い方:
  DATABASE_URL=postgresql://... python scripts/init_schema_v4.py

冪等（何度実行しても安全）。Postgres 専用。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import schema_v4

if __name__ == "__main__":
    schema_v4.init_v4()
