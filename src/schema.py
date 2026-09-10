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
    """CREATE TABLE IF NOT EXISTS user_snapshots (
        snapshot_id      TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        schema_version   TEXT,
        will_text        TEXT,
        state_json       TEXT,
        supporting_json  TEXT,
        necessity_json   TEXT,
        src_input_hash   TEXT,
        vulnerable_hidden INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS policy_consents (
        user_id        TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        agreed_at      TEXT NOT NULL,
        PRIMARY KEY (user_id, policy_version)
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

    # ── 台帳 v2 基盤（指示書17 §3・§4）────────────────────────────
    # マジックリンク認証。token は平文保存しない（token_hash のみ）。
    """CREATE TABLE IF NOT EXISTS auth_identities (
        subject_id  TEXT PRIMARY KEY,
        email_hash  TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS auth_tokens (
        token_hash  TEXT PRIMARY KEY,
        email_hash  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        used_at     TEXT
    )""",
    # 追記専用イベント列。書き込みは ledger_events.append_event のみ（§4-4）。
    """CREATE TABLE IF NOT EXISTS ledger_events (
        seq           INTEGER PRIMARY KEY,
        at            TEXT NOT NULL,
        actor         TEXT NOT NULL,
        type          TEXT NOT NULL,
        prev_hash     TEXT,
        canon_version TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        event_hash    TEXT NOT NULL UNIQUE
    )""",
    # 是認ログ（署名）。本段階では空。署名の動線は後から（§2-1）。
    """CREATE TABLE IF NOT EXISTS attestations (
        event_hash    TEXT NOT NULL,
        by            TEXT NOT NULL,
        pubkey        TEXT NOT NULL,
        sig           TEXT NOT NULL,
        at            TEXT NOT NULL,
        level         TEXT NOT NULL,
        canon_version TEXT NOT NULL,
        PRIMARY KEY (event_hash, by, pubkey)
    )""",
    # 日次アンカー（root）。external_ref は段階4では null（§6）。
    """CREATE TABLE IF NOT EXISTS anchors (
        anchor_seq    INTEGER PRIMARY KEY,
        date          TEXT NOT NULL UNIQUE,
        from_seq      INTEGER NOT NULL,
        to_seq        INTEGER NOT NULL,
        root          TEXT NOT NULL,
        prev_anchor   TEXT,
        external_ref  TEXT
    )""",
    # 通常DB（削除自由・§5-7）。成立の前段（片方向の承認・招待）はここに置き、台帳に載せない。
    """CREATE TABLE IF NOT EXISTS connection_requests (
        id           TEXT PRIMARY KEY,
        from_subject TEXT NOT NULL,
        to_subject   TEXT NOT NULL,
        necessity_id TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL,
        responded_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS invitations (
        id           TEXT PRIMARY KEY,
        ctx          TEXT NOT NULL,
        inviter      TEXT NOT NULL,
        invitee      TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL,
        responded_at TEXT
    )""",

    # ── 必要像の 1:N 化（指示書17 §7）────────────────────────────
    # 必要像は主体にも意志形成にも従属しない独立レコード（(C)案）。owner_ref は
    # subject_id でも intent_id でもよい（owner_kind で区別）。
    # will_vec / necessity_vec はベクトル化配線が入るまで TEXT(JSON) で保持（§9 で報告。
    # pgvector 化は照合切替＝後続段で。次元の早期固定を避ける）。
    """CREATE TABLE IF NOT EXISTS necessities (
        necessity_id    TEXT PRIMARY KEY,
        owner_ref       TEXT NOT NULL,
        owner_kind      TEXT NOT NULL,
        n               INTEGER NOT NULL,
        will_text       TEXT NOT NULL,
        will_vec        TEXT,
        necessity_text  TEXT NOT NULL,
        necessity_vec   TEXT,
        gate_s          REAL,
        gate_u          REAL,
        p_sharpness     REAL,
        alpha           REAL,
        beta            REAL,
        evidence_commit TEXT,
        content_hash    TEXT NOT NULL,
        origin          TEXT NOT NULL,
        generator       TEXT,
        prev_necessity  TEXT,
        created_at      TEXT NOT NULL
    )""",
    # evidence_span の平文と salt（削除可能・§7-2）。salt を消せばコミットメントは開けなくなる。
    """CREATE TABLE IF NOT EXISTS necessity_evidence (
        necessity_id  TEXT PRIMARY KEY,
        evidence_span TEXT,
        salt          TEXT
    )""",
    # 意志形成の本文置き場（指示書23 §1-5）。台帳は content_hash のみ・本文は可読性のためここへ。
    """CREATE TABLE IF NOT EXISTS intent_content (
        intent_id        TEXT PRIMARY KEY,
        ctx              TEXT NOT NULL,
        body             TEXT,
        declaration_json TEXT,
        result           TEXT,
        updated_at       TEXT NOT NULL
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
    """CREATE TABLE IF NOT EXISTS user_snapshots (
        snapshot_id      TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        schema_version   TEXT,
        will_text        TEXT,
        state_json       TEXT,
        supporting_json  TEXT,
        necessity_json   TEXT,
        src_input_hash   TEXT,
        vulnerable_hidden INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS policy_consents (
        user_id        TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        agreed_at      TEXT NOT NULL,
        PRIMARY KEY (user_id, policy_version)
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

    # ── 台帳 v2 基盤（指示書17 §3・§4）。SQLite 版と同一スキーマ（型は TEXT/INTEGER で共通）──
    """CREATE TABLE IF NOT EXISTS auth_identities (
        subject_id  TEXT PRIMARY KEY,
        email_hash  TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS auth_tokens (
        token_hash  TEXT PRIMARY KEY,
        email_hash  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        used_at     TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ledger_events (
        seq           INTEGER PRIMARY KEY,
        at            TEXT NOT NULL,
        actor         TEXT NOT NULL,
        type          TEXT NOT NULL,
        prev_hash     TEXT,
        canon_version TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        event_hash    TEXT NOT NULL UNIQUE
    )""",
    """CREATE TABLE IF NOT EXISTS attestations (
        event_hash    TEXT NOT NULL,
        by            TEXT NOT NULL,
        pubkey        TEXT NOT NULL,
        sig           TEXT NOT NULL,
        at            TEXT NOT NULL,
        level         TEXT NOT NULL,
        canon_version TEXT NOT NULL,
        PRIMARY KEY (event_hash, by, pubkey)
    )""",
    """CREATE TABLE IF NOT EXISTS anchors (
        anchor_seq    INTEGER PRIMARY KEY,
        date          TEXT NOT NULL UNIQUE,
        from_seq      INTEGER NOT NULL,
        to_seq        INTEGER NOT NULL,
        root          TEXT NOT NULL,
        prev_anchor   TEXT,
        external_ref  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS connection_requests (
        id           TEXT PRIMARY KEY,
        from_subject TEXT NOT NULL,
        to_subject   TEXT NOT NULL,
        necessity_id TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL,
        responded_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS invitations (
        id           TEXT PRIMARY KEY,
        ctx          TEXT NOT NULL,
        inviter      TEXT NOT NULL,
        invitee      TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL,
        responded_at TEXT
    )""",
    # ── 必要像の 1:N 化（指示書17 §7）。SQLite 版と同一（vec は TEXT/JSON で保持）──
    """CREATE TABLE IF NOT EXISTS necessities (
        necessity_id    TEXT PRIMARY KEY,
        owner_ref       TEXT NOT NULL,
        owner_kind      TEXT NOT NULL,
        n               INTEGER NOT NULL,
        will_text       TEXT NOT NULL,
        will_vec        TEXT,
        necessity_text  TEXT NOT NULL,
        necessity_vec   TEXT,
        gate_s          REAL,
        gate_u          REAL,
        p_sharpness     REAL,
        alpha           REAL,
        beta            REAL,
        evidence_commit TEXT,
        content_hash    TEXT NOT NULL,
        origin          TEXT NOT NULL,
        generator       TEXT,
        prev_necessity  TEXT,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS necessity_evidence (
        necessity_id  TEXT PRIMARY KEY,
        evidence_span TEXT,
        salt          TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS intent_content (
        intent_id        TEXT PRIMARY KEY,
        ctx              TEXT NOT NULL,
        body             TEXT,
        declaration_json TEXT,
        result           TEXT,
        updated_at       TEXT NOT NULL
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
        _migrate_user_snapshots(con)   # 既存テーブルに schema_version / vulnerable_hidden を後付け（指示書12改訂）
    print(f"[schema] init complete ({'postgres' if is_postgres() else f'sqlite:{db_path}'})")


def _migrate_user_snapshots(con) -> None:
    """user_snapshots に後付け列を idempotent に追加（PR#19 で作成済みの既存テーブル向け）。"""
    adds = [("schema_version", "TEXT"), ("vulnerable_hidden", "INTEGER NOT NULL DEFAULT 0")]
    if is_postgres():
        for name, typ in adds:
            con.execute(f"ALTER TABLE user_snapshots ADD COLUMN IF NOT EXISTS {name} {typ}")
    else:
        cols = {r[1] for r in con.execute("PRAGMA table_info(user_snapshots)").fetchall()}
        for name, typ in adds:
            if name not in cols:
                con.execute(f"ALTER TABLE user_snapshots ADD COLUMN {name} {typ}")
