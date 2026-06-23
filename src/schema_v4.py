"""
PoX v4 embedding接続システム — DB スキーマ定義（仕様書 B章）

非破壊: 既存テーブル（profiles/seekers/vessels/messages/communities）に触れない。
v4 専用テーブル（profiles_v4/profile_vectors/derived_necessity/ledger_v4）のみ作成。
Step 6（プラットフォーム移行）で投稿口が差し替わるまで既存フローと並存する。

Postgres 専用: vector / JSONB / HNSW / BIGSERIAL は SQLite 非対応。
"""
import os, sys
from db_connect import get_connection, is_postgres

# ── 未確定パラメータ（仕様書 H章）の初期値（外出し必須。ハードコードして散らさない）──
DEFAULT_MODEL_TAG = "qwen3-embedding-0.6b-d1024"  # H-1 実機確定後に更新
FULL_DIM  = 1024  # Qwen3-0.6B 固定。別モデルは別 model_tag で管理（B-2 複合キー）
SHORT_DIM = 256   # MRL 短次元（H-3 実機で損失 <1% を確認後に変更可）

# ── テーブル DDL（依存順: profiles_v4 → vectors/necessity/ledger）──────────────

_DDL_PROFILES_V4 = f"""
CREATE TABLE IF NOT EXISTS profiles_v4 (
    id                   TEXT PRIMARY KEY,
    schema_version       TEXT NOT NULL DEFAULT 'v4',
    will_text            TEXT NOT NULL,
    state_have           TEXT,
    state_can_type       TEXT,
    state_bound          TEXT,
    state_unsorted       TEXT,
    supporting_raw       JSONB NOT NULL,
    supporting_redacted  JSONB,
    pii_redaction_status TEXT NOT NULL DEFAULT 'pending',
    migrated_from        TEXT,
    confidence           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

# will_symmetric と will_passage は同じ意志テキストを別 prefix でエンコードした別ベクトル（混同禁止）
_DDL_PROFILE_VECTORS = f"""
CREATE TABLE IF NOT EXISTS profile_vectors (
    profile_id       TEXT    NOT NULL REFERENCES profiles_v4(id) ON DELETE CASCADE,
    model_tag        TEXT    NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT true,
    will_symmetric   vector({FULL_DIM}),
    will_passage     vector({FULL_DIM}),
    state_passage    vector({FULL_DIM}),
    necessity_query  vector({FULL_DIM}),
    will_sym_256     vector({SHORT_DIM}),
    will_pas_256     vector({SHORT_DIM}),
    state_pas_256    vector({SHORT_DIM}),
    necessity_q_256  vector({SHORT_DIM}),
    embedded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, model_tag)
)"""

_DDL_DERIVED_NECESSITY = """
CREATE TABLE IF NOT EXISTS derived_necessity (
    profile_id               TEXT    NOT NULL REFERENCES profiles_v4(id) ON DELETE CASCADE,
    model_tag                TEXT    NOT NULL,
    necessity_text           TEXT    NOT NULL,
    is_generated             BOOLEAN NOT NULL DEFAULT true,
    gate_s                   REAL,
    gate_u                   REAL,
    gamma                    REAL,
    p_sharpness              REAL,
    alpha                    REAL,
    beta                     REAL,
    evidence_span            TEXT,
    src_input_hash           TEXT,
    generator_prompt_version TEXT,
    generator_model_tag      TEXT,
    generated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, model_tag)
)"""

_DDL_LEDGER_V4 = """
CREATE TABLE IF NOT EXISTS ledger_v4 (
    id           BIGSERIAL PRIMARY KEY,
    seeker_id    TEXT NOT NULL REFERENCES profiles_v4(id),
    candidate_id TEXT NOT NULL REFERENCES profiles_v4(id),
    event        TEXT NOT NULL,
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

# ── インデックス ──────────────────────────────────────────────────────────────
# HNSW: 256次元・候補側カラムのみ（仕様 B-2）
# necessity_q_256 は query 側のため候補インデックス不要（B-2 注記）
# 照合クエリは必ず WHERE model_tag = :tag AND is_active = true で絞る（跨プール禁止 I章）
_DDL_HNSW_INDEXES = [
    f"CREATE INDEX IF NOT EXISTS idx_pv_will_sym_256  ON profile_vectors USING hnsw (will_sym_256  vector_cosine_ops)",
    f"CREATE INDEX IF NOT EXISTS idx_pv_state_pas_256 ON profile_vectors USING hnsw (state_pas_256 vector_cosine_ops)",
    f"CREATE INDEX IF NOT EXISTS idx_pv_will_pas_256  ON profile_vectors USING hnsw (will_pas_256  vector_cosine_ops)",
]
_DDL_BTREE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_pv_model_active ON profile_vectors (model_tag, is_active)"
)


def init_v4() -> None:
    """
    v4 テーブルと HNSW インデックスを Postgres に作成（冪等）。
    Postgres 以外では中断する（vector / JSONB / BIGSERIAL は SQLite 非対応）。

    使い方:
      DATABASE_URL=postgresql://... python scripts/init_schema_v4.py
    """
    if not is_postgres():
        print("[schema_v4] ERROR: Postgres 以外では実行できません。")
        print("  DATABASE_URL=postgresql://... python scripts/init_schema_v4.py")
        sys.exit(1)

    # テーブル作成（単一トランザクション。失敗は全体ロールバック）
    with get_connection() as con:
        con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        con.execute(_DDL_PROFILES_V4)
        con.execute(_DDL_PROFILE_VECTORS)
        con.execute(_DDL_DERIVED_NECESSITY)
        con.execute(_DDL_LEDGER_V4)
        con.execute(_DDL_BTREE_INDEX)

    # HNSW インデックス（個別トランザクション。pgvector 0.5+ 必須）
    # 空テーブルなら即完了。実データが多い場合は CONCURRENTLY を検討。
    hnsw_ok = 0
    for sql in _DDL_HNSW_INDEXES:
        try:
            with get_connection() as con:
                con.execute(sql)
            hnsw_ok += 1
        except Exception as e:
            print(f"[schema_v4] WARNING: HNSW インデックス作成失敗（pgvector 0.5+ 必須）: {e}")

    print("[schema_v4] init_v4 完了。")
    print(f"  テーブル : profiles_v4, profile_vectors, derived_necessity, ledger_v4")
    print(f"  HNSW     : {hnsw_ok}/{len(_DDL_HNSW_INDEXES)} 本作成"
          f" (will_sym_256 / state_pas_256 / will_pas_256, SHORT_DIM={SHORT_DIM})")
    print(f"  model_tag: {DEFAULT_MODEL_TAG}  FULL={FULL_DIM}  SHORT={SHORT_DIM}")
    print(f"  注意: 照合は WHERE model_tag=... AND is_active=true で絞ること（I章 跨プール禁止）")
