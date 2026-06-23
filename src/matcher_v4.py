"""
PoX v4 照合エンジン（E章 / Step 5）

E-1: 256-dim shortlist（Step 6 で DB HNSW クエリに置換）
E-2: 全次元 nested complement power mean でスコア算出
E-3: 律速軸・寄与率 attribution

チャネル定義（B-2 混同禁止）:
  a : will_symmetric 対 will_symmetric  — 共鳴（同じ方向への意志）
  b : necessity_query 対 state_passage  — 主補完（必要像 vs 候補の現状）
  c : will_passage   対 will_passage    — 意志補完（γ でゲート）

ネスト公式（Sakana #5）:
  complement = M_p([guard(b), guard(c)], [1.0, γ], p)
  final      = M_p([guard(a), complement], [α, β],  p)
"""
import math

from embedding_service import cosine, guard
from match_config import (
    GAMMA_EPS, P_SHARPNESS_DEFAULT, ALPHA_DEFAULT, BETA_DEFAULT, SHORTLIST_K,
)


# ── E-2: power mean ─────────────────────────────────────────────────────────
def power_mean(values, weights, p):
    """
    加重べき乗平均 M_p。
    p→0: 幾何平均（ソフトAND）、p<0: min 寄り、p>0: max 寄り。
    values は guard() 済みの正値を期待（p<0 でゼロを渡すと壊れる）。
    """
    if len(values) != len(weights):
        raise ValueError(f"values/weights 長さ不一致: {len(values)} vs {len(weights)}")
    W = sum(weights)
    if W <= 0:
        raise ValueError("weights の和が 0 以下")
    w = [wi / W for wi in weights]

    if abs(p) < 1e-9:
        # p≈0: 幾何平均 exp(Σ w_i * log(v_i))
        return math.exp(sum(wi * math.log(v) for wi, v in zip(w, values)))
    return (sum(wi * (v ** p) for wi, v in zip(w, values))) ** (1.0 / p)


# ── E-2: nested complement スコア ────────────────────────────────────────────
def score_candidate(seeker_vecs, candidate_vecs, gamma,
                    p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """
    全次元 nested complement power mean で最終スコアを計算（E-2）。

    seeker_vecs    : {"will_symmetric", "will_passage", "necessity_query", ...}
    candidate_vecs : {"will_symmetric", "will_passage", "state_passage", ...}
    gamma          : c チャネルのゲート重み（necessity_gen が算出）
    """
    a_sim = cosine(seeker_vecs["will_symmetric"], candidate_vecs["will_symmetric"])
    b_sim = cosine(seeker_vecs["necessity_query"], candidate_vecs["state_passage"])
    c_sim = cosine(seeker_vecs["will_passage"],    candidate_vecs["will_passage"])

    ga = guard(a_sim)
    gb = guard(b_sim)
    gc = guard(c_sim)

    complement = power_mean([gb, gc], [1.0, gamma], p)
    return power_mean([ga, complement], [alpha, beta], p)


# ── E-3: 律速軸・寄与率 attribution ─────────────────────────────────────────
def attribution(seeker_vecs, candidate_vecs, gamma,
                p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """
    各チャネルの寄与と律速軸を返す（E-3）。

    log 寄与は p=0 幾何平均の分解で解釈する（p 非依存の安定した軸判定）。
    律速軸: 加重 log 寄与が最も小さい（最も足を引っ張る）チャネル。
    gamma <= GAMMA_EPS のとき c チャネルを律速候補から外す。
    """
    a_sim = cosine(seeker_vecs["will_symmetric"], candidate_vecs["will_symmetric"])
    b_sim = cosine(seeker_vecs["necessity_query"], candidate_vecs["state_passage"])
    c_sim = cosine(seeker_vecs["will_passage"],    candidate_vecs["will_passage"])

    ga = guard(a_sim)
    gb = guard(b_sim)
    gc = guard(c_sim)

    complement = power_mean([gb, gc], [1.0, gamma], p)
    final = power_mean([ga, complement], [alpha, beta], p)

    # p=0 対数分解: log(final) = a_log + b_log + c_log
    ab_w   = alpha + beta
    comp_w = 1.0 + gamma
    a_log = (alpha / ab_w) * math.log(ga)
    b_log = (beta  / ab_w) * (1.0   / comp_w) * math.log(gb)
    c_log = (beta  / ab_w) * (gamma / comp_w) * math.log(gc) if gamma > GAMMA_EPS else 0.0

    contribs = {"a": a_log, "b": b_log, "c": c_log}
    limiting = min(contribs, key=lambda k: contribs[k])

    return {
        "a_sim": a_sim, "b_sim": b_sim, "c_sim": c_sim,
        "ga": ga, "gb": gb, "gc": gc,
        "complement": complement,
        "final": final,
        "a_log_contrib": a_log,
        "b_log_contrib": b_log,
        "c_log_contrib": c_log,
        "limiting_axis": limiting,
    }


# ── E-1: shortlist（256-dim 近傍 / Step 6 で DB HNSW に置換）────────────────
def shortlist(seeker_q256, candidates_256, k=SHORTLIST_K):
    """
    256-dim コサインで近傍 k 件を絞る（E-1 一次絞り込み）。

    seeker_q256    : seeker の necessity_q_256 ベクトル
    candidates_256 : [(candidate_id, vec_256), ...]
    戻り値         : candidate_id リスト（コサイン降順 k 件）
    """
    scored = [(cid, cosine(seeker_q256, v256)) for cid, v256 in candidates_256]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:k]]


# ── E-1+E-2: shortlist → full-dim re-rank ─────────────────────────────────
def rank_candidates(seeker_vecs, candidate_vecs_list, gamma,
                    p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT,
                    top_k=None):
    """
    全次元スコアで候補をランキングして返す（E-1+E-2 統合）。

    seeker_vecs         : build_vectors 戻り dict（necessity_query 含む）
    candidate_vecs_list : [(candidate_id, vecs_dict), ...]
    top_k               : 上位 N 件に絞る（None = 全件）
    戻り値              : [{"candidate_id", "score", "attribution"}, ...] 降順
    """
    results = []
    for cid, cvecs in candidate_vecs_list:
        sc   = score_candidate(seeker_vecs, cvecs, gamma, p, alpha, beta)
        attr = attribution(seeker_vecs, cvecs, gamma, p, alpha, beta)
        results.append({"candidate_id": cid, "score": sc, "attribution": attr})
    results.sort(key=lambda x: x["score"], reverse=True)
    if top_k is not None:
        results = results[:top_k]
    return results
