#!/usr/bin/env python3
"""
PoX 照合 — query 単位を「人」から「必要像」へ（指示書17 §7-5）。

**matcher_v4 の内部（ベクトル演算・冪平均結合・ゲート・prefix・MRL）には一切触れない。**
query を引く単位だけを変える。すなわち:

  seeker 側の3本 = will_symmetric（a）/ necessity_query（b）/ will_passage（c）

のうち、will_symmetric と necessity_query を **必要像レコード** から、will_passage を
**主体レコード（owner の 1:1 ベクトル）** から取り、frozen な rank_candidates を呼ぶ。

数値（γ/p/α/β）も必要像レコードのものを使う。γ は保存された gate_s/gate_u から
necessity_gen.compute_gamma（frozen）で導出する（match_v4 と同一の求め方）。

個人（owner=subject・必要像1本）の場合、この経路は人起点の match_v4 と同一結果になる
（seeker 側3本が一致するため）。1:N はコミュニティの目的別必要像で初めて効く。
"""
from matcher_v4 import rank_candidates
from necessity_gen import compute_gamma


def seeker_vecs_from_necessity(nec_query_vectors: dict, owner_will_passage) -> dict:
    """必要像の query 側2本＋主体の will_passage から seeker ベクトル束を組む。"""
    return {
        "will_symmetric": nec_query_vectors["will_symmetric"],   # a チャネル
        "necessity_query": nec_query_vectors["necessity_query"],  # b チャネル
        "will_passage": owner_will_passage,                       # c チャネル（主体側 1:1）
    }


def rank_for_necessity(necessity_numbers: dict, nec_query_vectors: dict,
                       owner_will_passage, candidate_list, *, top_k=None):
    """必要像を query として候補をランキング（rank_candidates は不変・§7-5）。

    necessity_numbers: {gate_s, gate_u, p_sharpness, alpha, beta}
    nec_query_vectors: {will_symmetric, necessity_query}
    candidate_list   : [(candidate_id, vecs_dict), ...]
    """
    gamma = compute_gamma(necessity_numbers.get("gate_s") or 0.0,
                          necessity_numbers.get("gate_u") or 0.0)
    p = necessity_numbers.get("p_sharpness") or 0.0
    alpha = necessity_numbers.get("alpha")
    beta = necessity_numbers.get("beta")
    alpha = 1.0 if alpha is None else alpha
    beta = 1.0 if beta is None else beta
    seeker_vecs = seeker_vecs_from_necessity(nec_query_vectors, owner_will_passage)
    return rank_candidates(seeker_vecs, candidate_list, gamma,
                           p=p, alpha=alpha, beta=beta, top_k=top_k)
