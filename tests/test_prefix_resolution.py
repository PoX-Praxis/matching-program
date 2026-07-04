"""
フェーズ3 バグ修正① 検証 — _PREFIX_BY_MODEL のキーが MODEL_TAG と一致すること。

以前は Qwen3 のキーが "qwen3-emb-0.6b" で MODEL_TAG "qwen3-embedding-0.6b-d1024" と
不一致 → フォールバック経由で解決していた。修正後は3モデルとも直接ヒットする。
挙動不変（prefix 文字列が変わらない）ことも固定する。
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from embedding_config import _PREFIX_BY_MODEL, MODEL_DIMS, MODEL_TAG, PREFIX


def test_all_model_tags_have_direct_prefix_entry():
    # MODEL_DIMS に載る3モデルは _PREFIX_BY_MODEL に直接キーがある（フォールバック不要）
    for tag in MODEL_DIMS:
        assert tag in _PREFIX_BY_MODEL, f"{tag} の prefix が直接登録されていない"
        for role in ("symmetric", "query", "passage"):
            assert role in _PREFIX_BY_MODEL[tag]


def test_qwen3_prefix_values_unchanged():
    q = _PREFIX_BY_MODEL["qwen3-embedding-0.6b-d1024"]
    assert q["symmetric"] == ""
    assert q["passage"] == ""
    assert q["query"].startswith("Instruct: Given a person's need description")
    assert q["query"].endswith("Query: ")


def test_default_model_tag_resolves_directly():
    # 既定 MODEL_TAG は直接ヒットし、フォールバックと同一結果
    assert MODEL_TAG in _PREFIX_BY_MODEL
    assert PREFIX == _PREFIX_BY_MODEL[MODEL_TAG]


def test_embgemma_nomic_prefix_values_unchanged():
    assert _PREFIX_BY_MODEL["embgemma-300m"]["symmetric"] == "task: sentence similarity | query: "
    assert _PREFIX_BY_MODEL["nomic-emb-v2"]["passage"] == "search_document: "
