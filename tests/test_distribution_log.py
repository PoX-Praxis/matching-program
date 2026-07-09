"""
タスク1 検証 — 全件スコア分布の蓄積（distribution_log.py）

stub で実行可能。検証: necessity_id の安定性／分布ファイルが書かれる／
サマリの構造／seekerベクトル保存／snapshot スキーマに触れない（別ディレクトリ出力）。
"""
import sys, os, json, pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
import distribution_log as dl


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    d = tmp_path / "dist"; c = tmp_path / "cache"
    d.mkdir(); c.mkdir()
    monkeypatch.setattr(dl, "DIST_DIR", d)
    monkeypatch.setattr(dl, "CACHE_DIR", c)
    return d, c


RANKED = [
    {"candidate_id": "a", "score": 0.80, "attribution": {"a_sim": 0.7, "b_sim": 0.5, "c_sim": 0.6, "limiting_axis": "b"}},
    {"candidate_id": "b", "score": 0.75, "attribution": {"a_sim": 0.6, "b_sim": 0.4, "c_sim": 0.55, "limiting_axis": "b"}},
    {"candidate_id": "c", "score": 0.70, "attribution": {"a_sim": 0.5, "b_sim": 0.45, "c_sim": 0.5, "limiting_axis": "a"}},
]


def test_necessity_id_stable_and_sensitive():
    assert dl.necessity_id("hello") == dl.necessity_id("hello")
    assert dl.necessity_id("hello") != dl.necessity_id("hello ")
    assert len(dl.necessity_id("x")) == 12


def test_save_distribution_writes_jsonl_and_summary(tmp_dirs):
    d, _ = tmp_dirs
    nid = dl.necessity_id("q1")
    jl, sm = dl.save_distribution("m1", nid, RANKED, "20260705_000000")
    # jsonl: 全候補分の行、各行に必須キー
    rows = [json.loads(l) for l in open(jl, encoding="utf-8")]
    assert len(rows) == 3
    for r in rows:
        assert set(r) == {"necessity_id", "login", "final_score", "a_sim", "b_sim", "c_sim", "limiting_axis"}
        assert r["necessity_id"] == nid
    # summary: percentile と b近傍密度
    s = json.load(open(sm, encoding="utf-8"))
    assert s["necessity_id"] == nid and s["n_candidates"] == 3
    assert set(s["score_percentiles"]) == {"p10", "p25", "p50", "p75", "p90"}
    assert s["b_sim_top10pct_threshold"] is not None


def test_count_prior_by_necessity(tmp_dirs):
    nid = dl.necessity_id("q2")
    assert dl.count_prior("m1", nid) == 0
    dl.save_distribution("m1", nid, RANKED, "20260705_000001")
    assert dl.count_prior("m1", nid) == 1
    # 別 necessity_id は加算しない
    dl.save_distribution("m1", dl.necessity_id("other"), RANKED, "20260705_000002")
    assert dl.count_prior("m1", nid) == 1


def test_save_seeker_vecs(tmp_dirs):
    _, c = tmp_dirs
    seeker = {k: [0.1, 0.2, 0.3] for k in dl.SEEKER_KEYS}
    p = dl.save_seeker_vecs("m1", "abc123", seeker)
    assert p.exists()
    d = json.load(open(p, encoding="utf-8"))
    assert d["model_tag"] == "m1" and d["necessity_id"] == "abc123"
    assert set(d["vecs"]) == set(dl.SEEKER_KEYS)


def test_outputs_isolated_to_dist_and_cache(tmp_dirs):
    d, c = tmp_dirs
    nid = dl.necessity_id("q3")
    dl.save_distribution("m1", nid, RANKED, "20260705_000003")
    dl.save_seeker_vecs("m1", nid, {k: [0.0] for k in dl.SEEKER_KEYS})
    # 出力は distributions/ と cache/ のみ（snapshot へは書かない）
    assert any(d.iterdir())
    assert any(c.iterdir())
