"""
Step 1 DoD 検証テスト — v4 DB スキーマ定義（仕様書 B章）

束2a 更新: フル次元化・256系(列/HNSW)廃止・derived_necessity 履歴保存化。
Postgres 接続なしで実行可能な構造検証（DDL 文字列の検査）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema_v4 import (
    DEFAULT_MODEL_TAG, FULL_DIM,
    _DDL_PROFILES_V4, _DDL_PROFILE_VECTORS,
    _DDL_DERIVED_NECESSITY, _DDL_DERIVED_NECESSITY_UQ, _DDL_LEDGER_V4,
    _DDL_BTREE_INDEX,
)


def test_constants():
    """次元は embedding_config 由来（モデルにより 1024/768）。DDL に反映されること。"""
    assert isinstance(FULL_DIM, int) and FULL_DIM > 0
    assert f"vector({FULL_DIM})" in _DDL_PROFILE_VECTORS
    assert isinstance(DEFAULT_MODEL_TAG, str) and DEFAULT_MODEL_TAG


def test_profiles_v4_columns():
    ddl = _DDL_PROFILES_V4
    for c in ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted"):
        assert c in ddl
    assert "supporting_raw" in ddl and "JSONB" in ddl
    assert "supporting_redacted" in ddl and "pii_redaction_status" in ddl
    assert "migrated_from" in ddl and "confidence" in ddl
    # 束2a: ②非同期生成の状態
    assert "generation_status" in ddl and "generation_error" in ddl


def test_profile_vectors_full_only():
    """束2a: full 4本のみ。_256 は廃止。"""
    ddl = _DDL_PROFILE_VECTORS
    assert f"vector({FULL_DIM})" in ddl
    for c in ("will_symmetric", "will_passage", "state_passage", "necessity_query"):
        assert c in ddl
    for c in ("will_sym_256", "will_pas_256", "state_pas_256", "necessity_q_256"):
        assert c not in ddl, f"_256 列は廃止のはず: {c}"
    assert "PRIMARY KEY (profile_id, model_tag)" in ddl
    assert "REFERENCES profiles_v4(id) ON DELETE CASCADE" in ddl


def test_derived_necessity_history_structure():
    """束2a: 代理キー + superseded_at + generator_name + 部分ユニーク索引。"""
    ddl = _DDL_DERIVED_NECESSITY
    assert "id" in ddl and "BIGSERIAL PRIMARY KEY" in ddl
    assert "PRIMARY KEY (profile_id, model_tag)" not in ddl  # 複合PKは廃止
    assert "superseded_at" in ddl
    assert "generator_name" in ddl            # 利用者申告AI名（新列）
    assert "generator_model_tag" in ddl       # 従来意味（経路/生成モデル識別）
    for c in ("necessity_text", "gate_s", "gate_u", "gamma", "p_sharpness",
              "alpha", "beta", "evidence_span", "src_input_hash",
              "generator_prompt_version"):
        assert c in ddl
    # 有効行(superseded_at IS NULL)は1ペア最大1つを部分ユニーク索引で担保
    assert "superseded_at IS NULL" in _DDL_DERIVED_NECESSITY_UQ
    assert "UNIQUE INDEX" in _DDL_DERIVED_NECESSITY_UQ


def test_ledger_v4_structure():
    ddl = _DDL_LEDGER_V4
    assert "BIGSERIAL PRIMARY KEY" in ddl
    assert "seeker_id" in ddl and "candidate_id" in ddl and "event" in ddl
    assert "payload" in ddl and "JSONB" in ddl


def test_no_256_and_no_hnsw():
    """束2a: 256系・HNSW は schema から消えていること（stage1 フル総当たり）。"""
    import schema_v4
    assert not hasattr(schema_v4, "_DDL_HNSW_INDEXES"), "256-HNSW DDL は廃止のはず"
    assert not hasattr(schema_v4, "SHORT_DIM"), "SHORT_DIM 参照は schema から除去のはず"
    assert "256" not in _DDL_PROFILE_VECTORS
    assert "idx_pv_model_active" in _DDL_BTREE_INDEX  # btree は残す
