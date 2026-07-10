"""
PoX v4 embedding接続システム — DB スキーマ定義（仕様書 B章）

非破壊: 既存テーブル（profiles/seekers/vessels/messages/communities）に触れない。
v4 専用テーブル（profiles_v4/profile_vectors/derived_necessity/ledger_v4）のみ作成。

Postgres 専用: vector / JSONB / HNSW / BIGSERIAL は SQLite 非対応。

束2a（2026-07）変更:
  - フル768次元へ移行。profile_vectors の _256 カラム4本と 256-HNSW 索引を削除
    （MRL256 不成立・スケール戦略 stage1＝フル次元総当たり。再設計時は
     docs/production_design_notes.md §2/§3 参照）。照合は db_v4 でフル次元総当たり。
  - derived_necessity を履歴保存化（代理キー id + superseded_at + 部分ユニーク索引）。
    再生成で旧行を superseded_at で無効化し新行を積む。最新は superseded_at IS NULL。
  - profiles_v4 に generation_status / generation_error を追加（②非同期生成の状態）。
  - 破壊的スキーマ変更（次元変更・キー構造変更）は POX_ALLOW_VECTOR_TABLE_REBUILD=1
    かつ対象テーブル 0 件のときのみ drop→再作成。1件以上なら停止（データ保全）。
"""
import os, sys
from db_connect import get_connection, is_postgres

# ── 未確定パラメータ（仕様書 H章）は embedding_config に単一ソース化 ──────────
# vector 列サイズと embedding 次元は必ず一致しなければならないため、定数は共有する。
from embedding_config import MODEL_TAG as DEFAULT_MODEL_TAG, FULL_DIM

# ── テーブル DDL（依存順: profiles_v4 → vectors/necessity/ledger）──────────────

_DDL_PROFILES_V4 = """
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
    generation_status    TEXT NOT NULL DEFAULT 'preparing',
    generation_error     TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

# 既存 profiles_v4 に列を足す（冪等・非破壊）。generation_status/error は束2aで追加。
_DDL_PROFILES_V4_ADDCOLS = [
    "ALTER TABLE profiles_v4 ADD COLUMN IF NOT EXISTS generation_status TEXT NOT NULL DEFAULT 'preparing'",
    "ALTER TABLE profiles_v4 ADD COLUMN IF NOT EXISTS generation_error  TEXT",
]

# will_symmetric と will_passage は同じ意志テキストを別 prefix でエンコードした別ベクトル（混同禁止）
# 束2a: フル次元のみ（_256 は廃止）。
_DDL_PROFILE_VECTORS = f"""
CREATE TABLE IF NOT EXISTS profile_vectors (
    profile_id       TEXT    NOT NULL REFERENCES profiles_v4(id) ON DELETE CASCADE,
    model_tag        TEXT    NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT true,
    will_symmetric   vector({FULL_DIM}),
    will_passage     vector({FULL_DIM}),
    state_passage    vector({FULL_DIM}),
    necessity_query  vector({FULL_DIM}),
    embedded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, model_tag)
)"""

# 束2a: 履歴保存化。代理キー id + superseded_at（NULL=最新/有効行）。
# generator_name は利用者申告のAI名（user-supplied 経路のみ・nullable）。
# generator_model_tag は「経路/生成モデルの識別子」（従来意味を維持）。
_DDL_DERIVED_NECESSITY = """
CREATE TABLE IF NOT EXISTS derived_necessity (
    id                       BIGSERIAL PRIMARY KEY,
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
    generator_name           TEXT,
    superseded_at            TIMESTAMPTZ,
    generated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

# 「1ペア（profile_id, model_tag）につき有効行（superseded_at IS NULL）は最大1つ」を
# 部分ユニーク索引で DB レベルに担保（アプリのバグで現行版が2件並ぶ事故を防ぐ）。
_DDL_DERIVED_NECESSITY_UQ = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_dn_active "
    "ON derived_necessity (profile_id, model_tag) WHERE superseded_at IS NULL"
)

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
# 束2a: 256-HNSW は廃止（MRL256 不成立・stage1 フル次元総当たり）。
# 照合クエリは必ず WHERE model_tag = :tag AND is_active = true で絞る（跨プール禁止 I章）。
_DDL_BTREE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_pv_model_active ON profile_vectors (model_tag, is_active)"
)

_REBUILD_FLAG = "POX_ALLOW_VECTOR_TABLE_REBUILD"


# ── Postgres イントロスペクション（再作成ガード用）─────────────────────────────
def _table_exists(con, table):
    r = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s", (table,)
    ).fetchone()
    return r is not None


def _column_exists(con, table, col):
    r = con.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s", (table, col)
    ).fetchone()
    return r is not None


def _vector_dim(con, table, col):
    """profile_vectors の vector 列の宣言次元を返す（'vector(768)'→768）。無ければ None。"""
    r = con.execute(
        "SELECT format_type(a.atttypid, a.atttypmod) "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=%s AND a.attname=%s AND NOT a.attisdropped",
        (table, col)
    ).fetchone()
    if not r:
        return None
    t = r[0] if not isinstance(r, dict) else list(r.values())[0]
    import re
    m = re.search(r"vector\((\d+)\)", str(t))
    return int(m.group(1)) if m else None


def _row_count(con, table):
    r = con.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(r[0] if not isinstance(r, dict) else list(r.values())[0])


def _rebuild_allowed():
    return os.environ.get(_REBUILD_FLAG) == "1"


def _guarded_drop(con, table, reason):
    """
    破壊的スキーマ変更のための drop。REBUILD フラグ＋0件のときのみ実行。
    1件以上なら SystemExit で停止（データ保全）。フラグ無しも停止。
    """
    n = _row_count(con, table)
    if n > 0:
        print(f"[schema_v4] 停止: {table} に {n} 件のデータがあり再作成できません（{reason}）。")
        print("           データ移行が必要です。0件でない限り自動 drop はしません。")
        sys.exit(1)
    if not _rebuild_allowed():
        print(f"[schema_v4] 停止: {table} は再作成が必要（{reason}）だが {_REBUILD_FLAG}=1 が未設定。")
        print(f"           0件を確認済みなら {_REBUILD_FLAG}=1 を付けて再実行してください。")
        sys.exit(1)
    print(f"[schema_v4] 再作成: {table} を drop→再作成します。理由: {reason}（0件確認済・{_REBUILD_FLAG}=1）")
    con.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def init_v4() -> None:
    """
    v4 テーブルと索引を Postgres に作成（冪等）。Postgres 以外では中断。

    破壊的変更（profile_vectors の次元不一致 / derived_necessity のキー構造旧式）は
    POX_ALLOW_VECTOR_TABLE_REBUILD=1 かつ 0 件のときのみ drop→再作成する。
    """
    if not is_postgres():
        print("[schema_v4] ERROR: Postgres 以外では実行できません。")
        print("  DATABASE_URL=postgresql://... python scripts/init_schema_v4.py")
        sys.exit(1)

    with get_connection() as con:
        con.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # profiles_v4（先に作る／既存には列追加）
        con.execute(_DDL_PROFILES_V4)
        for sql in _DDL_PROFILES_V4_ADDCOLS:
            con.execute(sql)

        # profile_vectors: 次元不一致なら再作成（束2a: 768化）
        if _table_exists(con, "profile_vectors"):
            dim = _vector_dim(con, "profile_vectors", "will_symmetric")
            if dim is not None and dim != FULL_DIM:
                _guarded_drop(con, "profile_vectors",
                              f"次元変更 {dim}→{FULL_DIM}（束2a フル次元化）")
        con.execute(_DDL_PROFILE_VECTORS)

        # derived_necessity: 旧キー構造（id 列が無い＝複合PK時代）なら再作成（束2a: 履歴化）
        if _table_exists(con, "derived_necessity") and not _column_exists(con, "derived_necessity", "id"):
            _guarded_drop(con, "derived_necessity",
                          "キー構造変更（複合PK→代理キー+superseded_at 履歴化）")
        con.execute(_DDL_DERIVED_NECESSITY)
        con.execute(_DDL_DERIVED_NECESSITY_UQ)

        con.execute(_DDL_LEDGER_V4)
        con.execute(_DDL_BTREE_INDEX)

    print("[schema_v4] init_v4 完了。")
    print(f"  テーブル : profiles_v4, profile_vectors, derived_necessity(履歴化), ledger_v4")
    print(f"  次元     : FULL={FULL_DIM}（256系は廃止・stage1 フル次元総当たり）")
    print(f"  model_tag: {DEFAULT_MODEL_TAG}")
    print(f"  注意: 照合は WHERE model_tag=... AND is_active=true で絞ること（I章 跨プール禁止）")
