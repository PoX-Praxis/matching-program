"""
タスク2 機構検証 — MRL256 検証スクリプト（mrl_report.py）

stub で合成キャッシュ＋seekerベクトルを作り、analyze() の機構が壊れず妥当な指標を返すことを見る。
（実数値の意味はモデル依存＝PC実行。ここでは範囲・型・完走のみ検証。）
"""
import sys, os, json, pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval", "scripts"))

import pytest
import vector_cache
import mrl_report as mr
from embedding_service import build_vectors
from embedding_config import MODEL_TAG

CANDS = {
    "a": {"will_text": "実用ツールを作りたい", "state_have": "Python", "state_can_type": "作る人", "state_bound": "", "state_unsorted": ""},
    "b": {"will_text": "研究で知能を実現", "state_have": "機械学習", "state_can_type": "研究する人", "state_bound": "", "state_unsorted": ""},
    "c": {"will_text": "デザインで体験を良く", "state_have": "UI", "state_can_type": "作る人", "state_bound": "東京", "state_unsorted": ""},
    "d": {"will_text": "オープンソースで貢献", "state_have": "JS", "state_can_type": "発信する人", "state_bound": "", "state_unsorted": ""},
}
SEEKER = {"will_text": "接続の構造を作りたい", "state_have": "構造化", "state_can_type": "つなぐ人", "state_bound": "", "state_unsorted": ""}


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    snap_dir = tmp_path / "snap"; snap_dir.mkdir()
    monkeypatch.setattr(vector_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(mr, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(mr, "SNAP_DIR", snap_dir)
    monkeypatch.setattr(mr, "OUT", tmp_path / "mrl_report.md")
    # 候補キャッシュ（stub・full+256 を含む8ベクトル）
    cache = {}
    for cid, prof in CANDS.items():
        vector_cache.get_or_build(cid, prof, MODEL_TAG, cache)
    vector_cache.save_cache(MODEL_TAG, cache)
    # seeker ベクトル保存
    sv = build_vectors(SEEKER, "接続の必要像")
    import distribution_log as dl
    monkeypatch.setattr(dl, "CACHE_DIR", cache_dir)
    dl.save_seeker_vecs(MODEL_TAG, "testnid00000", sv)
    return MODEL_TAG


def test_analyze_returns_valid_metrics(synthetic):
    r = mr.analyze(synthetic)
    assert r is not None
    assert 0.0 <= r["jaccard"] <= 1.0
    assert 0 <= r["keep10"] <= 10
    assert r["verdict"] in ("PASS", "FAIL")
    assert set(r["channels"]) == {"a", "b", "c"}
    for ch in ("a", "b", "c"):
        assert 0.0 <= r["channels"][ch]["jaccard"] <= 1.0


def test_analyze_missing_data_returns_none(tmp_path, monkeypatch):
    empty = tmp_path / "empty"; empty.mkdir()
    monkeypatch.setattr(vector_cache, "CACHE_DIR", empty)
    monkeypatch.setattr(mr, "CACHE_DIR", empty)
    assert mr.analyze("nonexistent-model") is None
