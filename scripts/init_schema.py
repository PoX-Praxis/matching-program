#!/usr/bin/env python3
"""
PoX スキーマ初期化 CLI。

全テーブルを CREATE TABLE IF NOT EXISTS で作成する。
Postgres の場合は pgvector 拡張と seeker_embeddings テーブルも作成する。
冪等（何度実行しても安全）。

使い方:
  DATABASE_URL=postgresql://... python scripts/init_schema.py
  python scripts/init_schema.py            # SQLite（ローカル開発）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import schema

if __name__ == "__main__":
    db_path = os.environ.get("POX_DB", "pox.db")
    schema.init(db_path)
