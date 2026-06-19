#!/usr/bin/env python3
"""
PoX スキーマ定義。

init(db_path) を呼ぶと全テーブルを CREATE TABLE IF NOT EXISTS で作成する。
Postgres の場合は pgvector 拡張と seeker_embeddings テーブルも作成する。
冪等なので何度実行しても安全（再デプロイ時も安全）。

呼び出し元:
  - app.py: 起動時（DATABASE_URL がある場合に自動実行）
  - scripts/init_schema.py: CLI から手動実行
  - scripts/migrate_sqlite_to_pg.py: 移行前の準備
"""
import os
from db_connect import get_connection, is_postgres

# ── SQLite 用 DDL（既存スキーマと完全互換）─────────────────────

_SQLITE_DDL = [
    """CREATE TABLE IF NOT EXISTS seekers (
        id          TEXT PRIMARY KEY,
        seeker_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS profiles (
        user_id        TEXT PRIMARY KEY,
        seeker         TEXT NOT NULL,
        profile_view   TEXT NOT NULL,
        visibility     TEXT NOT NULL DEFAULT 'public',
        view_overrides TEXT NOT NULL DEFAULT '{}',
        created_at     TEXT,
        updated_at     TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS vessels (
        vessel_id   TEXT PRIMARY KEY,
        vessel_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id             TEXT PRIMARY KEY,
        from_id        TEXT NOT NULL,
        to_id          TEXT NOT NULL,
        body           TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL,
        is_read        INTEGER DEFAULT 0,
        attachment_url TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS communities (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        founder     TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS community_members (
        community_id TEXT NOT NULL,
        member_id    TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        joined_at    TEXT NOT NULL,
        PRIMARY KEY (community_id, member_id)
    )""",
]

# ── Postgres 用 DDL（pgvector 拡張 + seeker_embeddings を追加）────

_PG_DDL = [
    # pgvector 拡張（Render Postgres では利用可能）
    "CREATE EXTENSION IF NOT EXISTS vector",

    """CREATE TABLE IF NOT EXISTS seekers (
        id          TEXT PRIMARY KEY,
        seeker_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS profiles (
        user_id        TEXT PRIMARY KEY,
        seeker         TEXT NOT NULL,
        profile_view   TEXT NOT NULL,
        visibility     TEXT NOT NULL DEFAULT 'public',
        view_overrides TEXT NOT NULL DEFAULT '{}',
        created_at     TIMESTAMPTZ,
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS vessels (
        vessel_id   TEXT PRIMARY KEY,
        vessel_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id             TEXT PRIMARY KEY,
        from_id        TEXT NOT NULL,
        to_id          TEXT NOT NULL,
        body           TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL,
        is_read        SMALLINT DEFAULT 0,
        attachment_url TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS communities (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        founder     TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS community_members (
        community_id TEXT NOT NULL,
        member_id    TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        joined_at    TEXT NOT NULL,
        PRIMARY KEY (community_id, member_id)
    )""",

    # ── 将来の embedding 格納用（§3: 器だけ用意・今は空でよい）──────
    # 仕様 spec/matching_spec_v0.6.md 5.5節: MRL で 256 次元想定。
    # 次元数はモデル確定後に変更可（別テーブルなので既存データに影響なし）。
    # ANN インデックス（ivfflat / hnsw）は実データが溜まってから追加する。
    """CREATE TABLE IF NOT EXISTS seeker_embeddings (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        seeker_id  TEXT NOT NULL,
        element    TEXT NOT NULL,        -- '意志' / '求めている' / '能力' など（要素別マルチベクトル）
        embedding  vector(256),          -- MRL 256 次元想定（仕様 5.5 節）。確定前は変更しうる
        model_name TEXT,                 -- どのモデルで生成したか（再現性・移行用）
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
]


def init(db_path: str = "pox.db") -> None:
    """
    全テーブルを CREATE TABLE IF NOT EXISTS で作成する。
    Postgres の場合は pgvector 拡張と seeker_embeddings も作成する。
    冪等（何度呼んでも安全）。
    """
    ddl_list = _PG_DDL if is_postgres() else _SQLITE_DDL
    with get_connection(db_path) as con:
        for ddl in ddl_list:
            con.execute(ddl)
    print(f"[schema] init complete ({'postgres' if is_postgres() else f'sqlite:{db_path}'})")
