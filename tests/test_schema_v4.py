"""
Step 1 DoD 検証テスト — v4 DB スキーマ定義（仕様書 B章）

Postgres 接続なしで実行可能な構造検証。
実 Postgres での CREATE TABLE / HNSW 確認は init_schema_v4.py で行う。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema_v4 import (
    DEFAULT_MODEL_TAG, FULL_DIM, SHORT_DIM,
    _DDL_PROFILES_V4, _DDL_PROFILE_VECTORS,
    _DDL_DERIVED_NECESSITY, _DDL_LEDGER_V4,
    _DDL_HNSW_INDEXES, _DDL_BTREE_INDEX,
)


def test_constants():
    """H章の未確定値が定数として外出しされていること。"""
    assert FULL_DIM == 1024, "Qwen3-0.6B フル次元は 1024 (A-3 固定)"
    assert SHORT_DIM == 256, "MRL 短次元は 256 (H-3 実機確認待ち)"
    assert "qwen3" in DEFAULT_MODEL_TAG.lower(), "model_tag は Qwen3 を識別できること"
    assert "0.6b" in DEFAULT_MODEL_TAG.lower(), "MVP モデルは 0.6B (A-3 確定)"


def test_profiles_v4_columns():
    """B-1: profiles_v4 に raw/redacted 分離・PII ステータス・二軸テキストがあること。"""
    ddl = _DDL_PROFILES_V4
    # 本源二軸
    assert "will_text" in ddl
    assert "state_have" in ddl
    assert "state_can_type" in ddl
    assert "state_bound" in ddl
    assert "state_unsorted" in ddl
    # raw/redacted 分離（B-1 §Sakana #8）
    assert "supporting_raw" in ddl and "JSONB" in ddl
    assert "supporting_redacted" in ddl
    assert "pii_redaction_status" in ddl
    # legacy 移行用フラグ（F-5）
    assert "migrated_from" in ddl
    assert "confidence" in ddl


def test_profile_vectors_structure():
    """B-2: profile_vectors が複合キー・4本フル+4本短・正しい次元を持つこと。"""
    ddl = _DDL_PROFILE_VECTORS
    # フル次元 4 本
    assert f"vector({FULL_DIM})" in ddl
    assert "will_symmetric" in ddl    # a チャネル（対称）
    assert "will_passage" in ddl      # c チャネル（passage。a と混同禁止）
    assert "state_passage" in ddl     # b チャネル（相補・現状）
    assert "necessity_query" in ddl   # query 側
    # 短次元 4 本
    assert f"vector({SHORT_DIM})" in ddl
    assert "will_sym_256" in ddl
    assert "will_pas_256" in ddl
    assert "state_pas_256" in ddl
    assert "necessity_q_256" in ddl   # B-2 §Sakana #9
    # 複合キー（B-2 §Sakana #9 / モデル移行のため）
    assert "PRIMARY KEY (profile_id, model_tag)" in ddl
    assert "REFERENCES profiles_v4(id) ON DELETE CASCADE" in ddl


def test_derived_necessity_structure():
    """B-3: derived_necessity に ② 所有の連続パラメータと hash 管理があること。"""
    ddl = _DDL_DERIVED_NECESSITY
    assert "necessity_text" in ddl
    assert "is_generated" in ddl       # 生成物フラグ（本人申告と区別）
    # ② が seeker ごとに発行する連続パラメータ
    assert "gate_s" in ddl
    assert "gate_u" in ddl
    assert "gamma" in ddl
    assert "p_sharpness" in ddl
    assert "alpha" in ddl
    assert "beta" in ddl
    assert "evidence_span" in ddl      # γ 較正・説明用
    # 再生成 hash（B-3 §Sakana #6）
    assert "src_input_hash" in ddl
    assert "generator_prompt_version" in ddl
    assert "PRIMARY KEY (profile_id, model_tag)" in ddl


def test_ledger_v4_structure():
    """B-4: ledger_v4 が追記専用・監査メタ(payload JSONB)を持つこと。"""
    ddl = _DDL_LEDGER_V4
    assert "BIGSERIAL PRIMARY KEY" in ddl
    assert "seeker_id" in ddl
    assert "candidate_id" in ddl
    assert "event" in ddl    # 'suggested'|'approved'|'connected'|'succeeded'|'failed'
    assert "payload" in ddl and "JSONB" in ddl   # 監査メタ（B-4）


def test_hnsw_indexes():
    """B-2: HNSW は 256 次元・候補側 3 本のみ。necessity_q_256 は query 側なので不要。"""
    hnsw = [sql for sql in _DDL_HNSW_INDEXES if "hnsw" in sql.lower()]
    assert len(hnsw) == 3, (
        f"HNSW インデックスは候補側 3 本のみ（仕様 B-2）、実際: {len(hnsw)}"
    )
    indexed_cols = " ".join(hnsw)
    assert "will_sym_256" in indexed_cols
    assert "state_pas_256" in indexed_cols
    assert "will_pas_256" in indexed_cols
    # necessity_q_256 は query 側のためインデックス不要（B-2 注記 §Sakana #2）
    assert "necessity_q_256" not in indexed_cols, (
        "necessity_q_256 に HNSW を張ってはいけない（B-2 / query 側）"
    )
    # cosine 演算子で作成されていること
    for sql in hnsw:
        assert "vector_cosine_ops" in sql


def test_no_full_dim_hnsw():
    """B-2: フル次元 HNSW は MVP では張らない（§Sakana #3）。"""
    all_index_sql = " ".join(_DDL_HNSW_INDEXES) + _DDL_BTREE_INDEX
    assert f"vector({FULL_DIM})" not in all_index_sql, (
        f"フル次元({FULL_DIM})の HNSW は MVP では禁止（B-2 §Sakana #3）"
    )


if __name__ == "__main__":
    tests = [
        test_constants,
        test_profiles_v4_columns,
        test_profile_vectors_structure,
        test_derived_necessity_structure,
        test_ledger_v4_structure,
        test_hnsw_indexes,
        test_no_full_dim_hnsw,
    ]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 1 DoD テスト: {len(tests)} 件 全 PASS")
