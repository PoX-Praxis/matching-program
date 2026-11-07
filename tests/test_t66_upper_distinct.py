"""上層の連言の別の形（参考値）— 必須ごとに別の文で覆う（テスト 380）。本番の照合は変えない。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import upper_distinct_ref as U  # noqa: E402


# ── 380 必須ごとに互いに違う文を割り当てられるか ──────────────────────────────────────────
def test_t380_distinct_cover_assignment():
    assert U.covers_any([{"a"}, {"a"}]) and not U.covers_distinct([{"a"}, {"a"}])   # 1 文で 2 つの必須は不可
    assert U.covers_distinct([{"a"}, {"a", "b"}])                                   # a → 1 つ目、b → 2 つ目
    assert U.covers_distinct([{"a", "b"}, {"a"}])                                   # 割り当てをやり直す
    assert not U.covers_distinct([{"a", "b"}, {"a", "b"}, {"a", "b"}])               # 3 つの必須に 2 文
    assert not U.covers_distinct([set(), {"a"}]) and U.covers_distinct([])
    hits = [{"x"}, {"x"}, set()]
    assert U.purpose_ok(hits, [0, 1, 2], [0, 1], distinct=False) and not U.purpose_ok(hits, [0, 1, 2], [0, 1], distinct=True)
    assert U.purpose_ok(hits, [0, 1, 2], [], distinct=True)                          # 必須の無い目的は、どれかの要素が通れば
    import numpy as np
    import element_probe_v2 as E2
    G = E2.Group(np.array([[0.80, 0.72], [0.78, 0.75]]), np.array([0.7, 0.7]), np.array([0.5, 0.5]))
    P = U.upper_passes("R+b3", G, 0.70, [[0, 1], [0, 1]])          # b-3: その要素がいちばん近い文だけ
    assert P.tolist() == [[True, False], [False, True]] and U.upper_passes("R", G, 0.70, [[0, 1], [0, 1]]).all()
    src = open(os.path.join(ROOT, "scripts", "upper_distinct_ref.py"), encoding="utf-8").read()
    for k in ("DATABASE_URL", "X-Anchor-Token", "pox-web.onrender.com", "get_connection", "ledger"):
        assert k not in src, k
    assert "本番の照合（上層 0.70 のまま）" in src
