"""指示書17 §7-4/§7-5: 必要像ベクトル化 と「query 単位＝必要像」の照合。

- vectorize_necessity は build_vectors と同じ embed() を通す（テストは embed_fn 注入）。
- rank_for_necessity は frozen rank_candidates を呼ぶだけ。個人（1:1）では人起点の
  rank_candidates と **同一の順位・スコア** になる（query 側3本が一致するため）。
"""
import os, sys, tempfile, hashlib
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import necessities as N
import matching_necessity as MN
from matcher_v4 import rank_candidates
from necessity_gen import compute_gamma


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _vec(seed, dim=16):
    out = []
    c = 0
    while len(out) < dim:
        for byte in hashlib.sha256(f"{seed}:{c}".encode()).digest():
            if len(out) >= dim:
                break
            out.append(byte / 255.0)
        c += 1
    return out


# ── §7-4 ベクトル化 ─────────────────────────────────────────────────────────
def test_vectorize_uses_same_embed_path():
    db = _db()
    calls = []
    def fake_embed(text, role):
        calls.append((text, role))
        return _vec(f"{role}:{text}")
    r = N.publish_necessity("u1", "subject",
                            {"necessity_text": "翻訳できる開発者", "will_text": "つなぐ"},
                            db_path=db)
    nid = r["necessity_id"]
    assert N.query_vectors(nid, db_path=db) is None       # 未ベクトル化
    N.vectorize_necessity(nid, embed_fn=fake_embed, db_path=db)
    qv = N.query_vectors(nid, db_path=db)
    # will_symmetric は will_text を 'symmetric'、necessity_query は necessity_text を 'query'
    assert qv["will_symmetric"] == _vec("symmetric:つなぐ")
    assert qv["necessity_query"] == _vec("query:翻訳できる開発者")
    assert ("つなぐ", "symmetric") in calls and ("翻訳できる開発者", "query") in calls


def test_vectorize_default_embed_runs_with_stub_backend():
    db = _db()
    r = N.publish_necessity("u2", "subject",
                            {"necessity_text": "支える人", "will_text": "続ける"}, db_path=db)
    out = N.vectorize_necessity(r["necessity_id"], db_path=db)   # 既定 embed（stub）
    qv = N.query_vectors(r["necessity_id"], db_path=db)
    assert out["dim"] > 0
    assert len(qv["will_symmetric"]) == len(qv["necessity_query"]) == out["dim"]


# ── §7-5 query 単位＝必要像。個人は人起点と同一結果 ──────────────────────────
def test_necessity_query_matches_person_based_for_1to1():
    # 主体の 4 本ベクトル（人起点 match_v4 が使う束）
    V = {
        "will_symmetric": _vec("wsym"),
        "will_passage": _vec("wpas"),
        "state_passage": _vec("spas"),
        "necessity_query": _vec("nq"),
    }
    cands = [
        (cid, {"will_symmetric": _vec(f"{cid}-wsym"),
               "will_passage": _vec(f"{cid}-wpas"),
               "state_passage": _vec(f"{cid}-spas")})
        for cid in ("c1", "c2", "c3")
    ]
    nums = {"gate_s": 0.6, "gate_u": 0.3, "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0}
    gamma = compute_gamma(nums["gate_s"], nums["gate_u"])

    person = rank_candidates(V, cands, gamma, p=nums["p_sharpness"],
                             alpha=nums["alpha"], beta=nums["beta"])
    # 必要像起点: query 側2本を主体の該当ベクトルから、will_passage を主体側から
    nec_q = {"will_symmetric": V["will_symmetric"], "necessity_query": V["necessity_query"]}
    by_nec = MN.rank_for_necessity(nums, nec_q, V["will_passage"], cands)

    assert [r["candidate_id"] for r in person] == [r["candidate_id"] for r in by_nec]
    assert [round(r["score"], 9) for r in person] == [round(r["score"], 9) for r in by_nec]


def test_seeker_vecs_assembly():
    s = MN.seeker_vecs_from_necessity(
        {"will_symmetric": [1.0], "necessity_query": [2.0]}, [3.0])
    assert s == {"will_symmetric": [1.0], "necessity_query": [2.0], "will_passage": [3.0]}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nmatching-necessity テスト: {len(tests)} 件 全 PASS")
